"""AI provider：抠图（matting）与打标（tagger）。

三条硬规则（DECISIONS D7 —— 一切可降级）：

1. **可选依赖全部惰性导入**。Worker 镜像才装 rembg / torch；API 进程 import 本模块
   不应该炸，也不应该把 ONNX 拖进来（D5）。
2. **失败不外抛**。provider 内部吞掉异常，把原因塞进 ``error`` 字段返回；
   流水线据此决定 failed 还是降级 ready。
3. **不凭空造词**。打标只从用户自己的标签取值里挑候选（见 :class:`CandidateTag`），
   模型没本事发明一个用户没有的标签。

provider 选择由 :func:`build_matting` / :func:`build_tagger` 按配置组装，
配置装不上时静默退回 no-op —— 衣橱在没有 AI 的情况下也必须完整可用。
"""

from __future__ import annotations

import base64
import inspect
import io
import json
import logging
import re
from dataclasses import dataclass, field

from PIL import Image

log = logging.getLogger(__name__)


# ---------- 数据结构 ----------


@dataclass
class MattingResult:
    """抠图结果。``image`` 为 None 表示没抠成（未启用 / 依赖缺失 / 推理失败）。"""

    image: Image.Image | None
    provider: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.image is not None


@dataclass
class CandidateTag:
    """给模型的候选标签。

    ``parent_value`` 是「小类挂在大类下」的父取值（例如 风衣 → 外套），拼进提示词里
    能让模型分清「开衫（上装）」和「开衫（连衣裙）」这类同名词。
    """

    value_id: int
    category_name: str
    value: str
    parent_value: str | None = None

    @property
    def label(self) -> str:
        if self.parent_value:
            return f"{self.parent_value}/{self.value}"
        return self.value

    def describe(self) -> str:
        return f"{self.category_name}: {self.label}"


@dataclass
class TagSuggestion:
    """打标结果。``name`` 与 ``tag_value_ids`` 全空表示「没给出任何建议」。"""

    name: str | None = None
    tag_value_ids: list[int] = field(default_factory=list)
    confidence: float | None = None
    error: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.name and not self.tag_value_ids


# ---------- 抠图 ----------


class NoOpMatting:
    """不抠图。默认 provider —— 用户没装依赖时衣橱照样能用。"""

    name = "none"

    def remove(self, image: Image.Image) -> MattingResult:  # noqa: ARG002 - 签名一致即可
        return MattingResult(image=None, provider=self.name)


class RembgMatting:
    """rembg U²-Net 抠图。

    依赖 ``rembg`` + ``onnxruntime``，只装在 Worker 镜像里。模型在 ``new_session``
    时下载到 ``~/.u2net``（离线环境要预先放好，见开发说明「离线部署」）。
    """

    name = "rembg"

    def __init__(
        self,
        model: str = "u2net",
        *,
        alpha_matting: bool = True,
        foreground_threshold: int = 240,
        background_threshold: int = 10,
        erode_size: int = 10,
    ) -> None:
        self.model = model
        #: alpha matting：用 trimap 做边缘细化。U²-Net 出来的掩码边缘往往是「一刀切」
        #: 的硬边，遇到毛绒、针织这种半透边缘会显得很假；打开它才有真实的羽化过渡。
        self.alpha_matting = alpha_matting
        self.foreground_threshold = foreground_threshold
        self.background_threshold = background_threshold
        self.erode_size = erode_size
        self._session: object | None = None
        self._error: str | None = None

    def _ensure_session(self) -> None:
        if self._session is not None or self._error is not None:
            return
        try:
            from rembg import new_session
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - 可选依赖，缺了就降级
            # ⚠️ rembg 在缺 onnxruntime 后端的机器上 import 时会直接 sys.exit(1)，
            # 抛的是 SystemExit（BaseException），except Exception 接不住 —— 必须显式捕
            self._error = f"rembg 不可用：{exc}"
            log.warning("抠图不可用，降级为原图：%s", self._error)
            return
        try:
            self._session = new_session(self.model)
        except Exception as exc:  # noqa: BLE001 - 模型下不来 / 加载失败都算降级
            self._error = f"抠图模型 {self.model} 加载失败：{exc}"
            log.warning("%s", self._error)

    def remove(self, image: Image.Image) -> MattingResult:
        self._ensure_session()
        if self._error is not None:
            return MattingResult(image=None, provider=self.name, error=self._error)
        try:
            from rembg import remove as _remove

            output = _remove(
                image.convert("RGB"),
                session=self._session,
                alpha_matting=self.alpha_matting,
                alpha_matting_foreground_threshold=self.foreground_threshold,
                alpha_matting_background_threshold=self.background_threshold,
                alpha_matting_erode_size=self.erode_size,
            )
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - 推理失败也只降级，不掀桌
            return MattingResult(image=None, provider=self.name, error=f"抠图失败：{exc}")
        if not isinstance(output, Image.Image):
            return MattingResult(
                image=None, provider=self.name, error=f"抠图返回了意外类型：{type(output)!r}"
            )
        return MattingResult(image=output, provider=self.name)


# ---------- 打标 ----------


class NoOpTagger:
    """不打标。默认 provider —— 命名和标签全交给用户。"""

    name = "none"

    async def suggest(
        self,
        image: Image.Image,
        candidates: list[CandidateTag],
        existing_names: list[str] | None = None,
        kind: str = "garment",  # noqa: ARG002 - 接口一致即可，no-op 不看它
    ) -> TagSuggestion:
        return TagSuggestion()


async def call_tagger(
    tagger: object,
    image: Image.Image,
    candidates: list[CandidateTag],
    existing_names: list[str] | None = None,
    kind: str = "garment",
) -> TagSuggestion:
    """调一次打标，按 provider **实际签名**决定传哪些参数。

    两件事都可能不被支持：``existing_names``（老 provider 没跟上）、``kind``
    （第三方 provider 只有单品概念）。硬传会 TypeError，整条建议就没了 —— 这里先
    看一眼签名，只传它接得住的。
    """
    kwargs: dict[str, object] = {}
    try:
        params = inspect.signature(tagger.suggest).parameters  # type: ignore[attr-defined]
    except (TypeError, ValueError):  # builtin / 奇葩对象取不到签名
        params = {}
    accepts = {name for name, p in params.items() if p.kind != inspect.Parameter.POSITIONAL_ONLY}
    var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    if var_kw or "existing_names" in accepts:
        kwargs["existing_names"] = existing_names
    if var_kw or "kind" in accepts:
        kwargs["kind"] = kind
    return await tagger.suggest(image, candidates, **kwargs)  # type: ignore[attr-defined]


class ClipTagger:
    """CLIP 零样本打标（VLM 不可用时的兜底，见 D7）。

    依赖 ``torch`` + ``open_clip_torch``，同样只装在 Worker 镜像里。
    ⚠️ 公开 CLIP 权重是英文语料训练的，**中文标签上效果有限**；主力仍是 VLM，
    这里只保证「连 VLM 都没有时还能给出点东西」。
    """

    name = "clip"

    def __init__(
        self,
        model: str = "ViT-B-32",
        pretrained: str = "laion2b_s34b_b79k",
        threshold: float = 0.18,
    ) -> None:
        self.model_name = model
        self.pretrained = pretrained
        self.threshold = threshold
        self._ready = False
        self._error: str | None = None
        self._bundle: tuple[object, object, object] | None = None

    def _ensure(self) -> bool:
        if self._ready:
            return True
        if self._error is not None:
            return False
        try:
            import open_clip  # type: ignore[import-not-found]
            import torch  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            self._error = f"CLIP 依赖未安装：{exc}"
            log.warning("%s", self._error)
            return False
        try:
            model, _, preprocess = open_clip.create_model_and_transforms(
                self.model_name, pretrained=self.pretrained
            )
            model.eval()
            self._bundle = (model, open_clip.get_tokenizer(self.model_name), preprocess)
            self._torch = torch  # type: ignore[attr-defined]
            self._ready = True
            return True
        except Exception as exc:  # noqa: BLE001
            self._error = f"CLIP 模型加载失败：{exc}"
            log.warning("%s", self._error)
            return False

    def _infer(self, image: Image.Image, candidates: list[CandidateTag]) -> list[tuple[int, float]]:
        assert self._bundle is not None
        model, tokenizer, preprocess = self._bundle
        torch = self._torch

        texts = [f"a photo of {c.label}" for c in candidates]
        with torch.no_grad():
            image_features = model.encode_image(preprocess(image.convert("RGB")).unsqueeze(0))
            text_features = model.encode_text(tokenizer(texts))
            image_features /= image_features.norm(dim=-1, keepdim=True)
            text_features /= text_features.norm(dim=-1, keepdim=True)
            probs = (image_features @ text_features.T).softmax(dim=-1)[0]

        scored = [(candidates[i].value_id, float(probs[i])) for i in range(len(candidates))]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored

    async def suggest(
        self,
        image: Image.Image,
        candidates: list[CandidateTag],
        existing_names: list[str] | None = None,
        kind: str = "garment",  # noqa: ARG002 - CLIP 不看图说话的方式，单品与搭配同流程
    ) -> TagSuggestion:
        # CLIP 只能比对标签、起不了名字，名字交给 VLM；existing_names 在这里用不上
        if not candidates:
            return TagSuggestion()
        if not self._ensure():
            return TagSuggestion(error=self._error)
        try:
            import asyncio

            scored = await asyncio.to_thread(self._infer, image, candidates)
        except Exception as exc:  # noqa: BLE001
            return TagSuggestion(error=f"CLIP 推理失败：{exc}")

        picked = [(vid, score) for vid, score in scored if score >= self.threshold]
        # 同一维度里塞一堆近义标签没意义，最多取 5 个
        picked = picked[:5]
        if not picked:
            return TagSuggestion(error="CLIP 没给出达到阈值的标签")
        return TagSuggestion(
            tag_value_ids=[vid for vid, _ in picked],
            confidence=round(sum(s for _, s in picked) / len(picked), 3),
        )


class VlmTagger:
    """OpenAI 兼容接口的视觉大模型（默认按 Qwen-VL 走，见 D8）。

    给模型的只有「图片 + 用户自己的标签候选」，要求它回一段 JSON；解析不出来就当没建议，
    绝不把模型自由发挥的文本当成标签写库。
    """

    name = "vlm"

    def __init__(
        self,
        base_url: str | None,
        api_key: str | None = None,
        model: str = "qwen-vl-max",
        timeout: float = 60.0,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.base_url)

    #: 提示词里最多回带多少件已有衣物的名字（太多了模型反而抓不住重点）
    NAME_HISTORY_LIMIT = 40

    @staticmethod
    def _name_rules(kind: str = "garment") -> str:
        """命名规则。单独抽出来是给测试用的：规则一改，测试跟着改。"""
        if kind == "outfit":
            return (
                "1. name：给这套搭配起一个**能和别的搭配区分开**的中文名，6~16 个字，"
                "按「风格或场景 + 主色或亮点单品」拼。\n"
                "   - 场景/风格：通勤、约会、周末休闲、度假、运动、学院、复古、甜美、极简；\n"
                "   - 主色：藏青、米白、黑白、焦糖、牛仔蓝…；\n"
                "   - 亮点单品：西装外套、百褶裙、马丁靴、针织马甲…\n"
                "   不要写「一套」「搭配」「穿搭」这类废词，也不要带品牌、价格。\n"
                "   参考：藏青西装通勤两件套、米白针织配百褶裙周末休闲、"
                "黑白极简衬衫阔腿裤通勤、牛仔蓝连衣裙度假风。\n"
            )
        return (
            "1. name：给这件衣物起一个**能和衣橱里其它衣服区分开**的中文名，8~18 个字，"
            "按「颜色 + 显著特征 + 品类」的顺序拼。\n"
            "   特征从下面挑最显眼的 1~2 个写进名字里（够区分就行，别堆词）：\n"
            "   - 颜色写具体：米白/浅灰/藏青/酒红/焦糖，拼色写「白底蓝条」；\n"
            "   - 图案：纯色/条纹/格纹/波点/印花/字母/刺绣/拼接；\n"
            "   - 材质：棉/麻/牛仔/羊毛/针织/真丝/皮质/羽绒/灯芯绒；\n"
            "   - 结构细节：圆领/V领/翻领/连帽、短袖/长袖/无袖、宽松/修身/oversize、"
            "短款/长款/高腰/直筒/阔腿/A字/百褶、厚底/细跟/平底；\n"
            "   - 品类写到最具体那一档：T恤/衬衫/卫衣/直筒牛仔裤/百褶半身裙…\n"
            "   不要写「一件」「衣服」这类废词，也不要带品牌、尺码、价格。\n"
            "   参考：藏青圆领纯棉短袖T恤、米白竖条纹宽松长袖衬衫、"
            "黑色高腰直筒水洗牛仔裤、焦糖色翻领羊毛长大衣。\n"
        )

    def _prompt(
        self,
        candidates: list[CandidateTag],
        existing_names: list[str] | None = None,
        kind: str = "garment",
    ) -> str:
        listing = "\n".join(f"- {c.describe()}" for c in candidates) or "（该用户还没有任何标签）"
        outfit = kind == "outfit"

        # 已有的名字回带给模型 —— 只靠「颜色+品类」必然撞名（三件黑 T 恤、两套牛仔通勤），
        # 让它看见已有名字，才知道要多写一个细节来区分。
        dedupe = ""
        if existing_names:
            shown = "、".join(existing_names[: self.NAME_HISTORY_LIMIT])
            if outfit:
                dedupe = (
                    f"\n补充要求：已经有这些搭配名：\n{shown}\n"
                    "你起的 name 必须和它们**不重复**；撞了就换一个角度写"
                    "（场景换说法，或把主色/亮点单品写进去）。\n"
                )
            else:
                dedupe = (
                    f"\n补充要求：衣橱里已经有这些名字：\n{shown}\n"
                    "你起的 name 必须和它们**不重复**；若只写「颜色+品类」会和上面某个名字撞车，"
                    "就再加一个细节（领型/袖长/图案/材质/版型/长度）来区分，"
                    "让人光看名字就能分清是两件衣服。\n"
                )

        if outfit:
            return (
                "你是穿搭助手。这是一张**搭配拼接图**：多件单品摆在一块背景上拼成一套，\n"
                "请按**整体效果**判断（别把它当成一件衣服，也别去描述背景颜色）。\n"
                f"{self._name_rules(kind)}"
                "2. tags：从候选标签里挑出**所有确实符合**这套搭配的，注意：\n"
                "   - 必须**原样抄**候选里的词，一个字都不要改，更不要自创；\n"
                "   - 季节按这套衣服的厚度与露肤度判断（羽绒/毛衣=冬，短袖短裤=夏）；\n"
                "   - 风格挑最像的那 1~2 个，别把一堆近义风格全选上；\n"
                "   - 色系取整体主色，撞色搭配取占比最大的那一组。\n"
                f"候选标签：\n{listing}\n"
                f"{dedupe}"
                "3. confidence：0 到 1 之间，你对上面判断的整体把握。\n"
                "只输出 JSON，不要任何解释，格式："
                '{"name": "...", "tags": ["取值1", "取值2"], "confidence": 0.8}'
            )

        return (
            "你是管理衣橱的助手。看清这张衣物图，回答三项信息。\n"
            f"{self._name_rules(kind)}"
            "2. tags：从候选标签里挑出**所有确实符合**的，注意：\n"
            "   - 必须**原样抄**候选里的词，一个字都不要改，更不要自创；\n"
            "   - 品类要挑到最具体的那一档，并且把它的上级一起带上"
            "（比如是 T 恤，就把「T恤」和它的上级类别都选上）；\n"
            "   - 颜色取肉眼最主色的那个，拼色的取底色；\n"
            "   - 材质只有看得准才选，拿不准宁可跳过。\n"
            f"候选标签：\n{listing}\n"
            f"{dedupe}"
            "3. confidence：0 到 1 之间，你对上面判断的整体把握。\n"
            "只输出 JSON，不要任何解释，格式："
            '{"name": "...", "tags": ["取值1", "取值2"], "confidence": 0.8}'
        )

    def _encode(self, image: Image.Image) -> str:
        # ⚠️ 先缩到 1024 长边再编码：VLM 只看版型/颜色/材质，用不上原图分辨率。
        # 而 15 MP 的手机原图转 JPEG + base64 是一个十几 MB 的字符串，每次打标都要
        # 在内存里完整拼一遍 —— 既慢又吃内存，还可能超模型的图片尺寸上限。
        img = image.convert("RGB")
        if max(img.size) > 1024:
            img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    async def suggest(
        self,
        image: Image.Image,
        candidates: list[CandidateTag],
        existing_names: list[str] | None = None,
        kind: str = "garment",
    ) -> TagSuggestion:
        """``existing_names`` 是已有的名字，用来让模型避开重名。可省略。

        ``kind`` 决定看图的口径：``garment`` = 一件单品，``outfit`` = 一套搭配拼接图。
        """
        if not self.available:
            return TagSuggestion(error="未配置 VLM 服务地址")
        try:
            import httpx
        except Exception as exc:  # noqa: BLE001
            # 这个分支只有镜像缺依赖时才会走到（历史事故：httpx 只写在
            # requirements-worker.txt 里，API 调 VLM 时炸）。文案指向修法，别只说「未安装」。
            return TagSuggestion(error=f"服务端缺少 httpx 依赖，需要重建镜像：{exc}")

        # 两本字典：模型可能回「上衣/T恤」（带父值），也可能只回「T恤」。
        # 后者在取值名不重复时同样能定位，别因为格式就丢掉标签。
        by_label = {c.label: c.value_id for c in candidates}
        by_value: dict[str, int] = {}
        for c in candidates:
            by_value.setdefault(c.value, c.value_id)
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{self._encode(image)}"},
                        },
                        {"type": "text", "text": self._prompt(candidates, existing_names, kind)},
                    ],
                }
            ],
            "temperature": 0.2,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions", json=payload, headers=headers
                )
                if resp.status_code >= 400:
                    return TagSuggestion(error=self._http_error(resp.status_code, resp.text))
                body = resp.json()
        except Exception as exc:  # noqa: BLE001 - 超时 / 连不上 / 返回非 JSON 都归到降级
            return TagSuggestion(error=f"VLM 调用失败：{exc}")

        content = self._extract_content(body)
        if not content:
            return TagSuggestion(error="VLM 返回内容为空")
        return self._parse(content, by_label, by_value)

    @staticmethod
    def _http_error(status: int, text: str) -> str:
        """把上游的报错说明带出来（截断）。

        配置出错时上游都会写明原因，比一个光秃秃的 400 有用得多：
        「model not found」（模型名写错）、「invalid content type」（填了不支持看图的
        纯文本模型）、「invalid api key」（密钥不对）、「insufficient balance」（没余额）。
        单独抽出来是为了能直接测，不必真起一个 HTTP 服务。
        """
        detail = " ".join(text.split())[:200] or "（无说明）"
        return f"VLM 返回 {status}：{detail}"

    @staticmethod
    def _extract_content(body: object) -> str:
        try:
            choices = body["choices"]  # type: ignore[index]
            message = choices[0]["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError):
            return ""
        if isinstance(content, list):  # 部分实现返回分块列表
            return "".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
        return str(content)

    @staticmethod
    def _loads(text: str) -> dict | None:
        """宽容地解析模型给的 JSON 对象。

        glm-4v-flash 这类小模型经常写出**缺逗号**的数组（``["鞋子" "黑"]``），
        一整条建议就这么被 json.loads 判死、白白丢掉名字和标签。这里补两步修复：
        先修中文引号，再补缺的逗号 / 去掉尾随逗号。
        """
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except ValueError:
            pass

        fixed = text.replace("\u201c", '"').replace("\u201d", '"')
        # 「"鞋子/芭蕾鞋"⏎"颜色/黑"」——两个字符串之间少了逗号
        fixed = re.sub(r'(?<=[}\]"\d])\s*(?=["{\[])', ",", fixed)
        # 「["a", ]」——尾随逗号
        fixed = re.sub(r",\s*(?=[}\]])", "", fixed)
        try:
            data = json.loads(fixed)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _parse(
        self,
        content: str,
        by_label: dict[str, int],
        by_value: dict[str, int] | None = None,
    ) -> TagSuggestion:
        """从模型输出里抠 JSON。带 ```json 代码块、前后有口水的都容忍。"""
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            return TagSuggestion(error=f"VLM 输出的不是 JSON：{content[:120]}")
        data = self._loads(match.group(0))
        if data is None:
            return TagSuggestion(error=f"VLM 输出的 JSON 无法解析：{match.group(0)[:120]}")

        name = data.get("name")
        raw_tags = data.get("tags")
        value_ids: list[int] = []
        if isinstance(raw_tags, list):
            for item in raw_tags:
                if not isinstance(item, str):
                    continue
                key = item.strip()
                value_id = by_label.get(key)
                if value_id is None:
                    # 模型偶尔会带类别前缀回来，剥掉再试一次
                    key = key.split(":")[-1].strip()
                    value_id = by_label.get(key)
                if value_id is None and by_value:
                    # 只回了取值名（如「T恤」而不是「上衣/T恤」），按名字直接找
                    value_id = by_value.get(key) or by_value.get(key.split("/")[-1].strip())
                if value_id is not None and value_id not in value_ids:
                    value_ids.append(value_id)

        name = str(name).strip()[:128] if isinstance(name, str) and name.strip() else None
        if not name and not value_ids:
            return TagSuggestion(error="VLM 没给出可用结果")

        # 置信度只有 0~1 之间的数字才认；模型信口开河给个 "high" 之类的一律忽略
        confidence: float | None = None
        raw_conf = data.get("confidence") if isinstance(data, dict) else None
        if isinstance(raw_conf, (int, float)) and 0 <= float(raw_conf) <= 1:
            confidence = round(float(raw_conf), 3)
        return TagSuggestion(name=name, tag_value_ids=value_ids, confidence=confidence)


# ---------- 组装 ----------


def build_matting(
    provider: str,
    model: str = "u2net",
    *,
    alpha_matting: bool = True,
    foreground_threshold: int = 240,
    background_threshold: int = 10,
    erode_size: int = 10,
):
    """按配置名造抠图 provider；认不出 / 装不上一律退回 no-op。"""
    if provider == "rembg":
        return RembgMatting(
            model,
            alpha_matting=alpha_matting,
            foreground_threshold=foreground_threshold,
            background_threshold=background_threshold,
            erode_size=erode_size,
        )
    if provider not in ("none", ""):
        log.warning("未知的抠图 provider %r，按 none 处理", provider)
    return NoOpMatting()


def build_tagger(
    provider: str,
    *,
    clip_model: str = "ViT-B-32",
    clip_pretrained: str = "laion2b_s34b_b79k",
    clip_threshold: float = 0.18,
    vlm_base_url: str | None = None,
    vlm_api_key: str | None = None,
    vlm_model: str = "qwen-vl-max",
    vlm_timeout: float = 60.0,
):
    """按配置名造打标 provider；认不出 / 装不上一律退回 no-op。"""
    if provider == "clip":
        return ClipTagger(clip_model, clip_pretrained, clip_threshold)
    if provider == "vlm":
        return VlmTagger(vlm_base_url, vlm_api_key, vlm_model, vlm_timeout)
    if provider not in ("none", ""):
        log.warning("未知的打标 provider %r，按 none 处理", provider)
    return NoOpTagger()


#: 进程级缓存：CLIP 要加载权重，每次请求重建既慢又浪费。
#: ⚠️ 只缓存**本地** provider。带密钥的 VLM 一律现造现用 —— 见 app/services/llm.py，
#: 缓存在那里会把 A 用户的 Key 缓存给 B 用户。
_clip_cache: ClipTagger | None = None


def shared_clip_tagger() -> ClipTagger:
    """本地 CLIP 的单例。它没有地址与密钥，属于机器能力，可以全局共用。"""
    from app.config import get_settings  # 惰性导入：providers 不该依赖配置模块

    global _clip_cache
    if _clip_cache is None:
        s = get_settings()
        _clip_cache = ClipTagger(s.clip_model, s.clip_pretrained, s.clip_threshold)
    return _clip_cache
