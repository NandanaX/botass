import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ChartFrame } from "./chart-frame";

describe("ChartFrame", () => {
  it("toggles full screen and exits with Escape", () => {
    render(<ChartFrame toolbar={<span>tools</span>}>{(full) => <div>{full ? "FULL" : "NORMAL"}</div>}</ChartFrame>);
    expect(screen.getByText("NORMAL")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Full screen" }));
    expect(screen.getByText("FULL")).toBeInTheDocument();
    expect(screen.getByText("FULL").closest(".fixed")).not.toBeNull();
    expect(screen.getByText("tools")).toBeInTheDocument(); // toggles stay available in full screen
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByText("NORMAL")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Full screen" }));
    fireEvent.click(screen.getByRole("button", { name: "Exit full screen" }));
    expect(screen.getByText("NORMAL")).toBeInTheDocument();
  });
});
