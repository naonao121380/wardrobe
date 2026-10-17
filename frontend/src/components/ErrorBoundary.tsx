import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = { children: ReactNode };
type State = { error: Error | null };

/**
 * 错误边界。
 *
 * 没有它的话，任何一个组件在渲染里抛错都会让整棵 React 树卸载 —— 用户看到的是一张
 * 白纸，既不知道发生了什么也没法自救。这里把错误收在页面主体之内，给出「重试」和
 * 「刷新」，并保留信息供排查。
 *
 * 注意边界**抓不到**：事件处理器里的异常、异步回调、服务端渲染。那些仍走各自的
 * try/catch 与 toast 提示。
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 留在控制台里 —— 排查时总得有个地方能看到 componentStack
    console.error("页面渲染出错：", error, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="card crash">
        <h3>页面出了点问题</h3>
        <p className="tiny">
          界面有一处渲染失败了。你的数据没有丢 —— 先「重试」，不行就刷新页面。
        </p>
        <pre>{error.message || String(error)}</pre>
        <div className="row-end" style={{ gap: 8, justifyContent: "center" }}>
          <button
            type="button"
            className="btn"
            onClick={() => {
              void navigator.clipboard?.writeText(`${error.message}\n${error.stack ?? ""}`);
            }}
          >
            复制错误信息
          </button>
          <button type="button" className="btn" onClick={() => window.location.reload()}>
            刷新页面
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => this.setState({ error: null })}
          >
            重试
          </button>
        </div>
      </div>
    );
  }
}
