// Publication year fallback. `papers.json` historically wrote `year: null`
// for every row, so we recover the year from the file_name stem
// (corpus convention: `LastAuthor_YYYY`, optionally with a disambiguator).
export function yearFromFileName(fileName: string | null | undefined): number | null {
  if (!fileName) return null;
  const m = fileName.match(/_(\d{4})(?:_|$)/);
  if (!m) return null;
  const y = Number(m[1]);
  if (y < 1900 || y > 2099) return null;
  return y;
}
