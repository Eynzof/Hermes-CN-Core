// @vitest-environment jsdom

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import { SelectOption } from "@nous-research/ui/ui/components/select";
import { PluginSelect } from "./registry";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | undefined;
let host: HTMLDivElement | undefined;

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  host?.remove();
  root = undefined;
  host = undefined;
});

describe("PluginSelect", () => {
  it("forwards stable accessibility attributes to the combobox trigger", async () => {
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);

    await act(async () => {
      root?.render(React.createElement(
        PluginSelect,
        {
          "aria-label": "Switch kanban board",
          "aria-describedby": "board-help",
          title: "Independent work streams",
          value: "default",
        },
        React.createElement(SelectOption, { value: "default", children: "Default" }),
      ));
    });

    const trigger = host.querySelector<HTMLButtonElement>('[role="combobox"]');
    expect(trigger?.getAttribute("aria-label")).toBe("Switch kanban board");
    expect(trigger?.getAttribute("aria-describedby")).toBe("board-help");
    expect(trigger?.getAttribute("title")).toBe("Independent work streams");
  });
});
