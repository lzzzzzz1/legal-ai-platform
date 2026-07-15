import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = {
  children: ReactNode;
};

type State = {
  hasError: boolean;
};

export default class AppErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("Legal AI 页面渲染失败", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <main className="app-fallback" role="alert">
          <p className="eyebrow">LEGAL AI</p>
          <h1>合同内容暂时无法显示</h1>
          <p>审查结果已返回，但其中的内容未能安全载入编辑器。刷新页面后可以重新上传或再次审查。</p>
          <button className="primary-button" type="button" onClick={() => window.location.reload()}>
            刷新页面
          </button>
        </main>
      );
    }

    return this.props.children;
  }
}
