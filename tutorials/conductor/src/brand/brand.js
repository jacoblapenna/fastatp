// Who the demo says it is. Change the name or the mark here and the page's
// header follows; index.html carries the tab's title.
export const brand = {
  name: 'FastATP',
  product: 'conductor',
};

// The goose: one bird seen from below, flying up the page, drawn in a
// 100 by 100 box. Every mark is made of this one path; public/favicon.svg
// carries a copy, and a test keeps the two the same.
export const goose =
  'M50 20C50.8 21.5 51.6 22.5 52.2 23.6C53.2 25 53.1 28 52 29.6C51.7 30.4 51.7 31.5 51.7 32.6L51.9 40.5C52 43 54 45.2 57 46.1C62 45.4 67 43.5 72 43.1C81 43.5 91 46.4 98 51L93.5 52L96.5 54.9L91.5 54.7L93 57.8L88 57C79 59.2 68 58.3 58 60.9C57.5 63.6 56.5 65.6 55.5 67.5L60 75.5C56 77.8 53 78.1 50 78.1C47 78.1 44 77.8 40 75.5L44.5 67.5C43.5 65.6 42.5 63.6 42 60.9C32 58.3 21 59.2 12 57L7 57.8L8.5 54.7L3.5 54.9L6.5 52L2 51C9 46.4 19 43.5 28 43.1C33 43.5 38 45.4 43 46.1C46 45.2 48 43 48.1 40.5L48.3 32.6C48.3 31.5 48.3 30.4 48 29.6C46.9 28 46.8 25 47.8 23.6C48.4 22.5 49.2 21.5 50 20Z';

// The skein: five geese in a V that points up, an A for the AT protocol.
// Each is [x, y] of a bird's middle in the same box; the first one leads.
export const skein = [[50, 25], [33, 50], [67, 50], [16.5, 75], [83.5, 75]];
export const skeinScale = 0.33;
