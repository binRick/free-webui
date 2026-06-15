import { listConversations, type ConversationSummary } from './api';

class ConvStore {
  list = $state<ConversationSummary[]>([]);

  async refresh(q?: string, archived = false): Promise<void> {
    this.list = await listConversations(q, archived);
  }
}

export const convs = new ConvStore();
