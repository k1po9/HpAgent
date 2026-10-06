export function conversationDateGroup(updatedAt: string, today = new Date()): string {
  const date = new Date(updatedAt);
  // Calendar arithmetic stays correct across DST transitions.
  const day = Date.UTC(date.getFullYear(), date.getMonth(), date.getDate());
  const now = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  const age = (now - day) / 86400000;
  return age <= 0 ? "今天" : age === 1 ? "昨天" : age < 7 ? "近 7 天" : "更早";
}
