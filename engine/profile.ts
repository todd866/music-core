// Types only. The R2-backed load/save lives in music-radio/lib/profile.ts; this file is the
// engine's view of a listener, so the engine package has no cloud dependency.
import type { Account } from './account';

export type Ev = { ts: number; s: string; ev: 'play' | 'skip' | 'like' | 'unlike' | 'finish' | 'more' | 'less' | 'dislike' | 'undislike'; via?: string; src?: 'ai' | 'control'; nw?: number; m?: string; pos?: number };
export type Profile = {
  user: string;
  qa?: boolean;
  style: 'home' | 'discovery';
  tz: string;
  newShare?: number;
  taste?: string[]; // first-run picker: genres this listener chose
  likes: string[];
  dislikes?: string[]; // never play (the thumbs-down button here, or a dislike in Glancebar)
  skips: Record<string, number>;
  heard: Record<string, number>;
  events: Ev[];
  updatedAt?: number;
};
export type AccountLike = Pick<Account, 'id' | 'style'>;
