import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { View } from "../types";

interface UiState {
  view: View;
  selectedSymbol: string;
  workspace: "trading" | "research" | "risk";
  commandPaletteOpen: boolean;
  setView: (view: View) => void;
  selectSymbol: (symbol: string) => void;
  setWorkspace: (workspace: UiState["workspace"]) => void;
  setCommandPaletteOpen: (open: boolean) => void;
}

export const useUiStore = create<UiState>()(persist(
  (set) => ({
    view: "desk",
    selectedSymbol: "BTC",
    workspace: "trading",
    commandPaletteOpen: false,
    setView: (view) => set({ view }),
    selectSymbol: (selectedSymbol) => set({ selectedSymbol: selectedSymbol.toUpperCase() }),
    setWorkspace: (workspace) => set({
      workspace,
      view: "desk",
    }),
    setCommandPaletteOpen: (commandPaletteOpen) => set({ commandPaletteOpen }),
  }),
  {
    name: "cryptoedge-ui-v1",
    partialize: ({ view, selectedSymbol, workspace }) => ({ view, selectedSymbol, workspace }),
  },
));
