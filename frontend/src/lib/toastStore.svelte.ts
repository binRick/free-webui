// Lightweight global toast store. `toasts.push(...)` from anywhere; the
// <Toasts /> container (mounted in the root layout) renders + auto-dismisses.
export type ToastKind = 'info' | 'error' | 'success';

export interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
}

class ToastStore {
  items = $state<Toast[]>([]);
  private seq = 0;

  push(message: string, kind: ToastKind = 'info', ttlMs = 4000): number {
    const id = ++this.seq;
    this.items = [...this.items, { id, kind, message }];
    if (ttlMs > 0) setTimeout(() => this.dismiss(id), ttlMs);
    return id;
  }

  error(message: string, ttlMs = 6000): number {
    return this.push(message, 'error', ttlMs);
  }

  success(message: string, ttlMs = 3000): number {
    return this.push(message, 'success', ttlMs);
  }

  dismiss(id: number): void {
    this.items = this.items.filter((t) => t.id !== id);
  }
}

export const toasts = new ToastStore();
