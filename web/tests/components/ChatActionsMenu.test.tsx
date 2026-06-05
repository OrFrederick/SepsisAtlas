import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatActionsMenu } from "../../src/components/ChatActionsMenu";

beforeEach(() => {
  // jsdom doesn't implement <dialog>.showModal/close. Reflect the `open`
  // property so the confirm dialog (and its buttons) are visible + clickable,
  // mirroring how the suite stubs other unsupported DOM APIs.
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
});

afterEach(() => cleanup());

describe("ChatActionsMenu", () => {
  it("renders the kebab trigger but no menu until clicked", () => {
    render(<ChatActionsMenu onClear={() => {}} />);
    expect(
      screen.getByRole("button", { name: /chat actions/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("opens a menu with a Clear chat item on click", async () => {
    const user = userEvent.setup();
    render(<ChatActionsMenu onClear={() => {}} />);
    await user.click(screen.getByRole("button", { name: /chat actions/i }));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: /clear chat/i }),
    ).toBeInTheDocument();
  });

  it("confirms before clearing: Cancel aborts", async () => {
    const onClear = vi.fn();
    const user = userEvent.setup();
    render(<ChatActionsMenu onClear={onClear} />);
    await user.click(screen.getByRole("button", { name: /chat actions/i }));
    await user.click(screen.getByRole("menuitem", { name: /clear chat/i }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onClear).not.toHaveBeenCalled();
  });

  it("calls onClear once the user confirms", async () => {
    const onClear = vi.fn();
    const user = userEvent.setup();
    render(<ChatActionsMenu onClear={onClear} />);
    await user.click(screen.getByRole("button", { name: /chat actions/i }));
    await user.click(screen.getByRole("menuitem", { name: /clear chat/i }));
    await user.click(screen.getByRole("button", { name: /clear chat/i }));
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it("closes the menu on Escape", async () => {
    const user = userEvent.setup();
    render(<ChatActionsMenu onClear={() => {}} />);
    await user.click(screen.getByRole("button", { name: /chat actions/i }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(
      screen.getByRole("button", { name: /chat actions/i }),
    ).toHaveFocus();
  });

  it("disables the trigger when disabled", () => {
    render(<ChatActionsMenu onClear={() => {}} disabled />);
    expect(
      screen.getByRole("button", { name: /chat actions/i }),
    ).toBeDisabled();
  });

  it("closes the confirm dialog on backdrop click without clearing", async () => {
    const onClear = vi.fn();
    const user = userEvent.setup();
    render(<ChatActionsMenu onClear={onClear} />);
    await user.click(screen.getByRole("button", { name: /chat actions/i }));
    await user.click(screen.getByRole("menuitem", { name: /clear chat/i }));
    // Clicking the <dialog> element itself = the ::backdrop region.
    fireEvent.click(screen.getByRole("dialog"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onClear).not.toHaveBeenCalled();
  });
});
