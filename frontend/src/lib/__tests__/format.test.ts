import { describe, expect, it } from "vitest";
import { formatServerTarget } from "../format";

describe("formatServerTarget (#37)", () => {
  it("keeps the classic host:port for plain hosts", () => {
    expect(formatServerTarget("sql01.acme.example", 1433)).toBe(
      "sql01.acme.example:1433",
    );
    expect(formatServerTarget("10.20.30.40", 1434)).toBe("10.20.30.40:1434");
  });

  it("renders a named instance bare — never HOST\\INST:1433", () => {
    expect(formatServerTarget("ATADBETSP001\\OISSQL", 1433)).toBe(
      "ATADBETSP001\\OISSQL",
    );
  });
});
