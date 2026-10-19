import { Component, ReactNode } from "react";

interface State {
  error: Error | null;
}

export default class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error("UI error:", error);
  }

  componentDidUpdate(prev: { resetKey?: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="panel panel-pad" role="alert">
        <h2>Не удалось показать этот блок</h2>
        <p className="muted" style={{ marginTop: 8 }}>
          Остальной интерфейс работает. Обновите страницу или вернитесь к ленте. Если ошибка повторяется, передайте
          команде текст ниже.
        </p>
        <pre
          className="small"
          style={{ whiteSpace: "pre-wrap", background: "var(--bg)", padding: 10, borderRadius: 6, marginTop: 10 }}
        >
          {this.state.error.message}
        </pre>
        <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
          <button className="btn" onClick={() => this.setState({ error: null })}>
            Попробовать снова
          </button>
          <a className="btn" href="#/feed">
            К ленте
          </a>
        </div>
      </div>
    );
  }
}
