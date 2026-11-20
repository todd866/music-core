import type { Track } from './space.ts';

// Genres for the first-run picker and for weighting a listener's queue.
export const WU = /wu-tang|gza|raekwon|ghostface|method man|ol' dirty|inspectah deck|rza|cappadonna|u-god|masta killa|sunz of man|killah priest/i;
export const UNDERGROUND = /mf doom|madvillain|j dilla|madlib|company flow|el-p|cannibal ox|aesop rock|dilated peoples|westside gunn|conway|benny the butcher|roc marciano|^ka$|billy woods|armand hammer|mach-hommy|earl sweatshirt|navy blue|freddie gibbs|run the jewels|danny brown|jpegmafia|griselda|quelle chris|open mike eagle/i;
export const GENRES: { id: string; label: string; member: (t: Track) => boolean }[] = [
  { id: 'hiphop', label: 'Hip hop', member: (t) => t.k === 'rap' || /hip-hop|rap/i.test(t.g) },
  { id: 'electronic', label: 'Electronic', member: (t) => t.k === 'dance' },
  { id: 'indie', label: 'Indie & alt', member: (t) => t.k === 'alt' && t.h < 0.8 },
  { id: 'rock', label: 'Rock', member: (t) => t.h > 0.8 || /rock|punk|metal/i.test(t.g) },
  { id: 'soul', label: 'Soul & R&B', member: (t) => /r&b|soul|funk/i.test(t.g) },
  { id: 'chill', label: 'Chill', member: (t) => t.k === 'chill' },
];
export const genreById = (id: string) => GENRES.find((g) => g.id === id);
