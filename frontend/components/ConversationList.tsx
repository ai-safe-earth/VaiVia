'use client';

import type { ConversationSummary } from '@/lib/conversations';

interface Props {
  conversations: ConversationSummary[];
  selected: string | null;
  onSelect: (id: string | null) => void;
}

export function ConversationList({ conversations, selected, onSelect }: Props) {
  return (
    <nav className="conversations" aria-label="Conversations">
      <button
        type="button"
        className={selected === null ? 'active' : ''}
        onClick={() => onSelect(null)}
      >
        + New chat
      </button>
      {conversations.map((conversation) => (
        <button
          key={conversation.id}
          type="button"
          className={selected === conversation.id ? 'active' : ''}
          onClick={() => onSelect(conversation.id)}
          title={conversation.title}
        >
          {conversation.title}
        </button>
      ))}
    </nav>
  );
}
