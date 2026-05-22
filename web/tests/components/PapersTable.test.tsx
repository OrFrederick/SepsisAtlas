import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PapersTable from "../../src/components/PapersTable";
import type { Paper } from "../../src/lib/types";

const papers: Paper[] = [
  {
    file_name: "Aardvark_2020",
    title: "A study",
    year: 2020,
    journal: null,
    parsed: true,
    n_rows: 10,
    verdicts: { ok: 5, weak: 3, fail: 2, unverified: 0 },
    last_update: "2024-01-02",
  },
  {
    file_name: "Zebra_2022",
    title: "Z study",
    year: 2022,
    journal: null,
    parsed: false,
    n_rows: 2,
    verdicts: { ok: 1, weak: 1, fail: 0, unverified: 0 },
    last_update: "2024-06-01",
  },
];

afterEach(cleanup);

function rowFileNames(): string[] {
  const tbody = screen.getByRole("table").querySelector("tbody")!;
  return Array.from(tbody.querySelectorAll("tr")).map(
    (tr) => tr.querySelector("td")!.textContent!.trim(),
  );
}

describe("PapersTable", () => {
  it("default sort is last_update desc", () => {
    render(<PapersTable papers={papers} basePath="/" />);
    expect(rowFileNames()).toEqual(["Zebra_2022", "Aardvark_2020"]);
  });

  it("clicking a column header toggles asc/desc", async () => {
    render(<PapersTable papers={papers} basePath="/" />);
    const fileHeader = screen.getByRole("columnheader", { name: /^File$/ });
    await userEvent.click(fileHeader);
    expect(rowFileNames()).toEqual(["Aardvark_2020", "Zebra_2022"]);
    await userEvent.click(fileHeader);
    expect(rowFileNames()).toEqual(["Zebra_2022", "Aardvark_2020"]);
  });

  it("numeric column sorts numerically", async () => {
    render(
      <PapersTable
        papers={[
          { ...papers[0], file_name: "A", n_rows: 2 },
          { ...papers[1], file_name: "B", n_rows: 10 },
        ]}
        basePath="/"
      />,
    );
    const header = screen.getByRole("columnheader", { name: /^Rows$/ });
    await userEvent.click(header);
    expect(rowFileNames()).toEqual(["A", "B"]);
    await userEvent.click(header);
    expect(rowFileNames()).toEqual(["B", "A"]);
  });

  it("each row links to /papers/<stem>/ with basePath applied", () => {
    render(<PapersTable papers={papers} basePath="/app/" />);
    const tbody = screen.getByRole("table").querySelector("tbody")!;
    const links = within(tbody as HTMLElement).getAllByRole("link");
    const hrefs = links.map((a) => a.getAttribute("href"));
    expect(hrefs).toContain("/app/papers/Zebra_2022/");
    expect(hrefs).toContain("/app/papers/Aardvark_2020/");
  });
});
