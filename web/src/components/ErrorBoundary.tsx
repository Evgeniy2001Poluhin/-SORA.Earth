import { Component, ReactNode } from 'react';
interface Props { children: ReactNode; fallback?: ReactNode }
interface State { hasError: boolean; error?: Error }
export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };
  static getDerivedStateFromError(error: Error) { return { hasError: true, error }; }
  componentDidCatch(error: Error, info: any) { console.error('[ErrorBoundary]', error, info); }
  render() {
    if (this.state.hasError) {
      return this.props.fallback ?? (
        <div className="min-h-[400px] flex flex-col items-center justify-center p-8 text-center">
          <h2 className="text-2xl font-bold text-red-400 mb-2">Something went ong</h2>
          <p className="text-sm text-gray-400 mb-4">{this.state.error?.message}</p>
          <button onClick={() => location.reload()} className="px-6 py-2 bg-green-500 hover:bg-green-400 text-black rounded-lg font-medium">
            Reload page
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
