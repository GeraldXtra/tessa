export const TOPICS = [
  'daemon.*',
  'agent.*',
  'audit.*',
  'pty.*',
  'transcript.*',
  'permission.*',
  'job.*',
  'voice.*',
  'turn.*',
  'notification',
] as const;

export function topicMatches(topic: string, type: string): boolean {
  const dot = type.indexOf('.');
  const rest = dot < 0 ? type : type.slice(dot + 1);
  if (topic === '*') return true;
  if (topic.endsWith('.*')) return rest.startsWith(topic.slice(0, -1));
  return rest === topic;
}

export function subscribedTo(type: string, topics: readonly string[] = TOPICS): boolean {
  return topics.some((t) => topicMatches(t, type));
}
