import { useEffect, useState, type ReactNode } from "react";

import Dialog from "../components/Dialog";

/**
 * 应用内确认弹窗 —— `window.confirm` 的替代品。
 *
 * 为什么不是每个调用点各写一个受控组件：调用点都在 `async` 处理器里，
 * 写成受控组件得为每处新增 `useState` + 提前 return 分支，五处的结构会各写一遍。
 * 这里给一个 Promise 版：`if (!(await confirmDialog({...}))) return;` ——
 * 调用点只改一行，样式与层级集中管理。
 */

export type ConfirmOptions = {
  title: string;
  /** 正文。要分段/加粗就传 JSX。 */
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** 危险操作（删除类）：主按钮换成红色，且默认焦点不给它。 */
  danger?: boolean;
};

type Pending = {
  opts: ConfirmOptions;
  resolve: (ok: boolean) => void;
};

let pending: Pending | null = null;
const listeners = new Set<(p: Pending | null) => void>();

function emit() {
  for (const l of listeners) l(pending);
}

function settle(current: Pending, ok: boolean) {
  // 只有「当前这一个」还挂着时才收口 —— 迟到的回调不该把新弹窗也关掉
  if (pending !== current) return;
  pending = null;
  emit();
  current.resolve(ok);
}

/**
 * 弹一个确认框，返回用户是否点了确认。
 * 点遮罩 / 按 Esc / 点取消都算 `false`。
 *
 * 同一时刻只留一个：若上一个还没答，先把它当「取消」收掉，
 * 免得 Promise 永远悬着（调用方的 `await` 就再也回不来了）。
 */
export function confirmDialog(opts: ConfirmOptions): Promise<boolean> {
  const prev = pending;
  if (prev) settle(prev, false);
  return new Promise<boolean>((resolve) => {
    pending = { opts, resolve };
    emit();
  });
}

/** 挂一次就够（App 根）。对话框渲染在这里，DOM 上排在路由之后，能盖住嵌套弹窗。 */
export function ConfirmHost() {
  const [cur, setCur] = useState<Pending | null>(pending);

  useEffect(() => {
    listeners.add(setCur);
    setCur(pending); // 订阅前可能已经有请求挂着了
    return () => {
      listeners.delete(setCur);
    };
  }, []);

  if (!cur) return null;
  const { opts } = cur;

  return (
    <Dialog
      title={opts.title}
      onClose={() => settle(cur, false)}
      width={440}
      footer={
        <>
          <button type="button" className="btn" onClick={() => settle(cur, false)}>
            {opts.cancelLabel ?? "取消"}
          </button>
          <button
            type="button"
            className={opts.danger ? "btn btn-danger" : "btn btn-primary"}
            onClick={() => settle(cur, true)}
            autoFocus={!opts.danger}
          >
            {opts.confirmLabel ?? "确定"}
          </button>
        </>
      }
    >
      {typeof opts.message === "string" ? <p>{opts.message}</p> : opts.message}
    </Dialog>
  );
}
