import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, api, type Meta } from "../api/client";
import { wardrobeApi } from "../api/wardrobe";
import { useAuth } from "../auth/AuthProvider";

type Stats = {
  garments: number;
  outfits: number;
  needsReview: number;
  failed: number;
  categories: number;
  values: number;
};

/** 「这里能做什么」。一句话说清，别写成功能清单 —— 用户是来穿衣服的，不是来读规格书的。 */
const FEATURES: { title: string; desc: string }[] = [
  {
    title: "上传即抠图",
    desc: "手机原图直接传，后台自动去背景；边缘会做羽化，毛绒、针织也不会切得像剪纸。",
  },
  {
    title: "AI 读图打标",
    desc: "配好视觉模型后，AI 给出名称、品类、颜色、场景的候选值，你只需要点头或改一改。",
  },
  {
    title: "拼搭配",
    desc: "把单品拖进画布拼成一套，调图层、导出成图；整套搭配也能让 AI 起名和打标。",
  },
  {
    title: "抠得不干净可以自己改",
    desc: "画笔擦掉残留、涂回被误删的部分；或者框一块区域，让模型对着那块重新算一次。",
  },
];

const STEPS: { title: string; desc: string }[] = [
  {
    title: "把衣服传进来",
    desc: "衣橱页右上角「批量上传」，一张图一件衣物，可以一次选很多张。",
  },
  {
    title: "复核 AI 的结果",
    desc: "首页的「待处理」亮起来就是有东西等你确认 —— 名称和标签，一件几秒。",
  },
  {
    title: "搭一套",
    desc: "搭配页新建一套，把衣物拖进画布；满意了就导出成图，或者存着当灵感。",
  },
];

function fmt(n: number | undefined): string {
  return n === undefined ? "—" : String(n);
}

export default function HomePage() {
  const { user } = useAuth();
  const [stats, setStats] = useState<Stats | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [garments, outfits, queue, cats] = await Promise.all([
          wardrobeApi.listGarments({ pageSize: 1 }),
          wardrobeApi.listOutfits({ pageSize: 1 }),
          wardrobeApi.queueStatus(),
          wardrobeApi.listCategories("garment"),
        ]);
        if (!alive) return;
        setStats({
          garments: garments.total,
          outfits: outfits.total,
          needsReview: queue.needs_review,
          failed: queue.failed,
          categories: cats.length,
          values: cats.reduce((n, c) => n + c.values.length, 0),
        });
      } catch (e) {
        if (alive) setErr(e instanceof ApiError ? e.message : "统计加载失败");
      }
    })();
    // 版本信息拿不到不算错，安静地不显示就是了
    api.meta().then((m) => alive && setMeta(m)).catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  const pending = stats ? stats.needsReview + stats.failed : 0;
  const empty = stats !== null && stats.garments === 0;
  const who = user?.display_name || user?.username;

  return (
    <>
      <section className="home-hero">
        <p className="home-eyebrow">衣柜 · 自托管</p>
        <h2 className="home-title">欢迎回来，{who}</h2>
        <p className="home-lead">
          把衣服拍成照片放进来，AI 替你抠掉背景、认出品类和颜色；想穿什么的时候，直接在网页上
          把单品拼成一套搭配。所有照片和数据都只存在你自己这台机器上。
        </p>
        <div className="home-actions">
          <Link className="btn btn-primary" to="/wardrobe">
            {empty ? "上传第一件衣服" : "进入衣橱"}
          </Link>
          <Link className="btn" to="/outfits">
            搭一套搭配
          </Link>
          {pending > 0 && (
            <Link className="home-nudge" to="/wardrobe?attention=1">
              有 {pending} 件等你复核
            </Link>
          )}
        </div>
      </section>

      <div className="stat-grid">
        <Link className="stat-card" to="/wardrobe">
          <div className="stat-num">{fmt(stats?.garments)}</div>
          <div className="stat-label">件衣物</div>
        </Link>
        <Link className="stat-card" to="/outfits">
          <div className="stat-num">{fmt(stats?.outfits)}</div>
          <div className="stat-label">套搭配</div>
        </Link>
        <Link className={`stat-card${pending > 0 ? " attn" : ""}`} to="/wardrobe?attention=1">
          <div className="stat-num">{fmt(stats ? pending : undefined)}</div>
          <div className="stat-label">件待处理（待复核 / 失败）</div>
        </Link>
        <Link className="stat-card" to="/wardrobe">
          <div className="stat-num">
            {fmt(stats?.categories)}
            <span className="stat-sub"> / {fmt(stats?.values)}</span>
          </div>
          <div className="stat-label">个标签类别 / 个取值</div>
        </Link>
      </div>

      {err && (
        <p className="tiny" style={{ marginBottom: 18 }}>
          {err}
        </p>
      )}

      <div className="card" style={{ marginBottom: 18 }}>
        <h3>这里能做什么</h3>
        <div className="home-features">
          {FEATURES.map((f) => (
            <div className="home-feature" key={f.title}>
              <div className="home-feature-t">{f.title}</div>
              <p>{f.desc}</p>
            </div>
          ))}
        </div>
      </div>

      <div className="card" style={{ marginBottom: 18 }}>
        <h3>第一次用，三步就够</h3>
        <ol className="home-steps">
          {STEPS.map((s) => (
            <li key={s.title}>
              <span className="home-step-t">{s.title}</span>
              <span className="home-step-d">{s.desc}</span>
            </li>
          ))}
        </ol>
      </div>

      <div className="card" style={{ marginBottom: 18 }}>
        <h3>AI 服务</h3>
        <dl className="about-grid">
          <dt>抠图</dt>
          <dd>
            {meta
              ? meta.matting_provider === "none"
                ? "未启用（上传后不做抠图，可手动修整）"
                : "本地模型，在后台 Worker 里跑"
              : "读取中…"}
          </dd>
          <dt>打标</dt>
          <dd>
            {meta ? (
              <>
                <span
                  className={`about-dot ${
                    meta.tagger_available ? "ok" : meta.tagger_provider === "none" ? "" : "off"
                  }`}
                  aria-hidden="true"
                />
                {meta.tagger_available
                  ? "已就绪，可自动命名与挑标签"
                  : "未配置 —— 命名和打标手动完成"}
              </>
            ) : (
              "读取中…"
            )}
          </dd>
        </dl>
        <p className="tiny" style={{ marginBottom: 0, marginTop: 10 }}>
          上传后可以先去做别的：AI 在后台排队处理，跑完衣橱页会提醒你。
        </p>
      </div>

      <p className="tiny home-foot">
        版本 v{meta?.version ?? "—"} · 数据与图片都保存在你自己的服务器上 · 当前账号{" "}
        {user?.username}
      </p>
    </>
  );
}
