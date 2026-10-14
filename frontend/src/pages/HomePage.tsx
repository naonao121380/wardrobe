import { useEffect, useState } from "react";

import { ApiError } from "../api/client";
import { wardrobeApi } from "../api/wardrobe";
import { useAuth } from "../auth/AuthProvider";

type Stats = {
  garments: number;
  categories: number;
  values: number;
};

const ROADMAP: { title: string; meta: string; done: boolean }[] = [
  {
    title: "批次 1 · 骨架与鉴权",
    meta: "登录 / 刷新令牌轮换 / 多设备会话 / 用户隔离",
    done: true,
  },
  {
    title: "批次 2 · 衣物与标签",
    meta: "衣物 CRUD、双标签体系、缩略图筛选墙、多图上传与封面",
    done: true,
  },
  {
    title: "批次 3 · AI 流水线",
    meta: "上传、抠图 Worker、打标 Provider、复核界面、CLIP 零样本",
    done: false,
  },
  {
    title: "批次 4 · 套装搭配编辑器",
    meta: "拖拽画布、图层、素材库、保存回看",
    done: false,
  },
  {
    title: "批次 5 · 收尾",
    meta: "删除级联提示、UI 打磨、测试、发版",
    done: false,
  },
];

export default function HomePage() {
  const { user } = useAuth();
  const [stats, setStats] = useState<Stats | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [list, cats] = await Promise.all([
          wardrobeApi.listGarments({ pageSize: 1 }),
          wardrobeApi.listCategories("garment"),
        ]);
        if (!alive) return;
        const values = cats.reduce((n, c) => n + c.values.length, 0);
        setStats({ garments: list.total, categories: cats.length, values });
      } catch (e) {
        if (alive) setErr(e instanceof ApiError ? e.message : "统计加载失败");
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  return (
    <>
      <div className="grid grid-2" style={{ marginBottom: 18 }}>
        <div className="card">
          <h3>欢迎回来，{user?.display_name || user?.username}</h3>
          <p>
            {stats
              ? `衣橱里已有 ${stats.garments} 件衣物、${stats.categories} 个标签类别（共 ${stats.values} 个取值）。`
              : err
                ? err
                : "正在读取衣橱数据…"}
          </p>
        </div>
        <div className="card">
          <h3>
            当前进度 <span className="badge badge-accent">批次 2 / 5</span>
          </h3>
          <p>衣物管理与标签体系已可用；下一步接入 AI 抠图与自动打标。</p>
        </div>
      </div>

      <h3 style={{ fontSize: 14, fontWeight: 600, margin: "0 0 10px" }}>开发路线</h3>
      <div className="list">
        {ROADMAP.map((item) => (
          <div className="list-row" key={item.title}>
            <div>
              <div className="title">
                {item.title}{" "}
                {item.done ? (
                  <span className="self-tag">已完成</span>
                ) : (
                  <span className="badge">待开发</span>
                )}
              </div>
              <div className="meta">{item.meta}</div>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
