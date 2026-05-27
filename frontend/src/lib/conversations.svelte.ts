import { listConversations, type ConversationSummary } from './api';

class ConvStore {
  list = $state<ConversationSummary[]>([]);

  async refresh(): Promise<void> {
    this.list = await listConversations();
  }
}

export const convs = new ConvStore();
