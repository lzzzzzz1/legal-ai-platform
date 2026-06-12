export type FuzzyMatch = {
  from: number;
  to: number;
  matchedText: string;
  similarity: number;
};

export const MISSING_SENTINEL = "【缺失该约定】";

const clausePrefixPattern =
  /^\s*(?:section\s+|article\s+)?(?:\(?\d+\)?|\(?[a-zA-Z]\)|[ivxlcdmIVXLCDM]+[.)]?|\d+(?:\.\d+)*[.)]?)\s*[:.)-]*\s*/i;

export function isMissingClause(originalText: string | undefined | null): boolean {
  if (!originalText) return true;
  const trimmed = originalText.trim();
  return trimmed === "" || trimmed === MISSING_SENTINEL || trimmed === "缺失该约定";
}

export function editDistance(s1: string, s2: string): number {
  const left = s1.toLowerCase();
  const right = s2.toLowerCase();
  const costs: number[] = [];

  for (let i = 0; i <= left.length; i += 1) {
    let lastValue = i;
    for (let j = 0; j <= right.length; j += 1) {
      if (i === 0) {
        costs[j] = j;
      } else if (j > 0) {
        let newValue = costs[j - 1];
        if (left.charAt(i - 1) !== right.charAt(j - 1)) {
          newValue = Math.min(Math.min(newValue, lastValue), costs[j]) + 1;
        }
        costs[j - 1] = lastValue;
        lastValue = newValue;
      }
    }
    if (i > 0) {
      costs[right.length] = lastValue;
    }
  }

  return costs[right.length];
}

export function getSimilarity(s1: string, s2: string): number {
  let longer = s1;
  let shorter = s2;
  if (s1.length < s2.length) {
    longer = s2;
    shorter = s1;
  }

  const longerLength = longer.length;
  if (longerLength === 0) {
    return 1;
  }

  return (longerLength - editDistance(longer, shorter)) / longerLength;
}

export function normalizeMatchText(text: string) {
  return text.toLowerCase().replace(/[\W_]+/g, " ").trim().replace(/\s+/g, " ");
}

export function stripClausePrefix(text: string) {
  return text.replace(clausePrefixPattern, "").trim();
}

export function extractHeadingCandidate(text: string) {
  const stripped = stripClausePrefix(text);
  if (!stripped) {
    return "";
  }

  const [heading] = stripped.split(/[.:\n;。；：]/, 1);
  return heading && heading.length <= 80 ? heading.trim() : "";
}

export function getParagraphMatchScore(paragraphText: string, query: string): number {
  const paragraphFull = normalizeMatchText(paragraphText);
  const queryFull = normalizeMatchText(query);

  if (!paragraphFull || !queryFull) {
    return 0;
  }

  if (paragraphFull === queryFull) {
    return 1;
  }

  if (paragraphFull.includes(queryFull)) {
    return 0.97;
  }

  const fullSimilarity = getSimilarity(paragraphFull, queryFull);
  const paragraphHeading = normalizeMatchText(extractHeadingCandidate(paragraphText));
  const queryHeading = normalizeMatchText(extractHeadingCandidate(query)) || queryFull;
  let headingSimilarity = 0;

  if (paragraphHeading) {
    if (paragraphHeading === queryHeading) {
      headingSimilarity = 0.96;
    } else if (paragraphHeading.includes(queryHeading) || queryHeading.includes(paragraphHeading)) {
      headingSimilarity = 0.93;
    } else {
      headingSimilarity = getSimilarity(paragraphHeading, queryHeading);
    }
  }

  return Math.max(fullSimilarity, headingSimilarity);
}

export function findFuzzyMatch(fullText: string, query: string, threshold = 0.8): FuzzyMatch | null {
  if (!query) {
    return null;
  }

  const exactIdx = fullText.indexOf(query);
  if (exactIdx >= 0) {
    return { from: exactIdx, to: exactIdx + query.length, matchedText: query, similarity: 1 };
  }

  const paragraphs = fullText.split("\n");
  let bestSim = 0;
  let bestParagraph = "";
  let currentOffset = 0;
  let bestOffset = -1;

  for (const paragraph of paragraphs) {
    const trimmed = paragraph.trim();
    if (trimmed.length > 0) {
      const sim = getParagraphMatchScore(trimmed, query);
      if (sim > bestSim) {
        bestSim = sim;
        bestParagraph = paragraph;
        bestOffset = currentOffset;
      }
    }
    currentOffset += paragraph.length + 1;
  }

  if (bestSim >= threshold && bestOffset >= 0) {
    return {
      from: bestOffset,
      to: bestOffset + bestParagraph.length,
      matchedText: bestParagraph,
      similarity: bestSim
    };
  }

  return null;
}
