import { describe, expect, it } from "vitest";
import { yearFromFileName } from "../../src/lib/paperYear";

describe("yearFromFileName", () => {
  it("extracts year from LastAuthor_YYYY stem", () => {
    expect(yearFromFileName("Baloch_2022")).toBe(2022);
    expect(yearFromFileName("Seymour_2016")).toBe(2016);
  });

  it("extracts year when a disambiguator follows", () => {
    expect(yearFromFileName("Li_2020_a")).toBe(2020);
    expect(yearFromFileName("Zhang_2021_predictor")).toBe(2021);
  });

  it("returns null for stems without a 4-digit year", () => {
    expect(yearFromFileName("NoYearHere")).toBeNull();
    expect(yearFromFileName("Author_99")).toBeNull();
    expect(yearFromFileName("")).toBeNull();
    expect(yearFromFileName(null)).toBeNull();
    expect(yearFromFileName(undefined)).toBeNull();
  });

  it("rejects 4-digit numbers outside a plausible publication range", () => {
    expect(yearFromFileName("Author_1700")).toBeNull();
    expect(yearFromFileName("Author_2200")).toBeNull();
  });
});
