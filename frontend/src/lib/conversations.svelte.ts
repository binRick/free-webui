import { listConversations, type ConversationSummary } from './api';

class ConvStore {
  list = $state<ConversationSummary[]>([]);

  async refresh(q?: string, archived = false, tag?: string): Promise<void> {
    this.list = await listConversations(q, archived, tag);
  }
}

export const convs = new ConvStore();
