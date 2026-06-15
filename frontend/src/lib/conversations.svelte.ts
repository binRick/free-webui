import { listConversations, type ConversationSummary } from './api';

class ConvStore {
  list = $state<ConversationSummary[]>([]);

  async refresh(
    q?: string,
    archived = false,
    tag?: string,
    folderId?: number | null
  ): Promise<void> {
    this.list = await listConversations(q, archived, tag, folderId);
  }
}

export const convs = new ConvStore();
