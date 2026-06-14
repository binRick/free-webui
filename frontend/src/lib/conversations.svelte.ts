import { listConversations, type ConversationSummary } from './api';

class ConvStore {
  list = $state<ConversationSummary[]>([]);

  async refresh(q?: string): Promise<void> {
    this.list = await listConversations(q);
  }
}

export const convs = new ConvStore();
