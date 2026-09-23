import { beforeEach, describe, expect, it, vi } from "vitest";

const readFromApiResult = vi.fn();
vi.mock("@/lib/api", () => ({ readFromApiResult: (path: string) => readFromApiResult(path) }));

const { resolveProgram } = await import("./programs");

const program = (id: string) => ({ id, name: id, operator: "", region: "" });

describe("resolveProgram", () => {
  beforeEach(() => readFromApiResult.mockReset());

  it("uses the only program when a deployment has one, so nobody chooses from a list of one", async () => {
    readFromApiResult.mockResolvedValue({ state: "ok", data: [program("program-a")] });
    const resolved = await resolveProgram();
    expect(resolved).toMatchObject({ state: "resolved", program: { id: "program-a" } });
  });

  it("offers the choice once a second organisation exists", async () => {
    readFromApiResult.mockResolvedValue({
      state: "ok",
      data: [program("program-a"), program("program-b")],
    });
    expect(await resolveProgram()).toMatchObject({ state: "choose" });
  });

  it("asks for exactly the program named, not the first one", async () => {
    readFromApiResult.mockResolvedValue({ state: "ok", data: program("program-b") });
    const resolved = await resolveProgram("program-b");
    expect(readFromApiResult).toHaveBeenCalledWith("/programs/program-b");
    expect(resolved).toMatchObject({ state: "resolved", program: { id: "program-b" } });
  });

  it("does not report a missing program when the API is unreachable", async () => {
    readFromApiResult.mockResolvedValue({ state: "unavailable" });
    expect(await resolveProgram("program-b")).toEqual({ state: "unavailable" });
    expect(await resolveProgram()).toEqual({ state: "unavailable" });
  });

  it("reports a named program that does not exist as missing", async () => {
    readFromApiResult.mockResolvedValue({ state: "missing" });
    expect(await resolveProgram("program-nope")).toEqual({ state: "none" });
  });
});
