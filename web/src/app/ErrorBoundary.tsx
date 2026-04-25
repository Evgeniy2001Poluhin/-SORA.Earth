import { Component, ReactNode, ErrorInfo } from "react";

interface Props { children: ReactNode; }
interface State { hasError: boolean; error: Error | null; }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };
  static getDerivedStateFromError(error: Error): State { return { hasError: true, error }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error("ErrorBoundary:", error, info); }
  reset = () => this.setState({ hasError: false, error: null });

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <div style={{display:"flex",alignItems:"center",justifyContent:"center",minHeight:"60vh",padding:"40px 20px"}}>
        <div style={{background:"#0E1218",border:"1px solid #1A2230",borderRadius:"16px",padding:"32px 40px",maxWidth:"520px",textAlign:"center"}}>
          <div style={{width:"48px",height:"48px",margin:"0 auto 20px",borderRadius:"50%",background:"rgba(239,68,68,0.1)",display:"flex",alignItems:"center",justifyContent:"center",border:"2px solid rgba(239,68,68,0.3)",fontSize:"24px"}}>⚠️</div>
          <h2 style={{fontSize:"20px",fontWeight:600,marginBottom:"8px"}}>Something went wrong</h2>
          <p style={{color:"var(--muted)",fontSize:"14px",marginBottom:"24px"}}>A component error occurred. Try again or reload.</p>
          <div style={{display:"flex",gap:"12px",justifyContent:"center"}}>
            <button onClick={this.reset} style={{background:"rgba(47,224,166,0.1)",border:"1px solid rgba(47,224,166,0.3)",color:"var(--planet)",padding:"10px 20px",borderRadius:"8px",cursor:"pointer"}}>Try again</button>
            <button onClick={() => location.reload()} style={{background:"#1A2230",border:"1px solid var(--line-2)",color:"var(--text)",padding:"10px 20px",borderRadius:"8px",cursor:"pointer"}}>Reload page</button>
          </div>
          {this.state.error && (
            <details style={{marginTop:"20px",textAlign:"left"}}>
              <summary style={{color:"var(--faint)",fontSize:"11px",cursor:"pointer",fontFamily:"var(--f-mono)",textTransform:"uppercase",letterSpacing:".1em"}}>Error details</summary>
              <pre style={{marginTop:"8px",padding:"12px",background:"#000",borderRadius:"6px",fontSize:"11px",color:"#999",overflow:"auto",maxHeight:"120px"}}>{this.state.error.message}</pre>
            </details>
          )}
        </div>
      </div>
    );
  }
}
