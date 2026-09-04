import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { engineAction, getStatus } from "../api";
import { useMarketStore } from "../state/marketStore";

export type EngineAction = (action: string, confirm?: boolean) => Promise<void>;

export function useEngine() {
  const [message, setMessage] = useState("");
  const replaceSnapshot = useMarketStore((state) => state.replaceSnapshot);
  const markDisconnected = useMarketStore((state) => state.markDisconnected);
  const snapshot = useMarketStore((state) => state.snapshot);
  const streamState = useMarketStore((state) => state.state);
  const status = useQuery({
    queryKey: ["engine-status"],
    queryFn: ({ signal }) => getStatus(signal),
    refetchInterval: streamState === "live" ? 15_000 : 1_000,
    refetchIntervalInBackground: true,
  });
  useEffect(() => {
    if (status.data) replaceSnapshot(status.data);
    else if (status.isError) markDisconnected();
  }, [status.data, status.isError, replaceSnapshot, markDisconnected]);
  const command = useMutation({
    mutationFn: ({ action, confirm }: { action: string; confirm: boolean }) => engineAction(action, confirm),
  });
  const act: EngineAction = async (action, confirm = false) => {
    const success: Record<string, string> = {
      start_trading: "Uruchamianie bota — postęp jest widoczny w Zdarzeniach.",
      stop: "Bot został zatrzymany.",
      close_all: "Wysłano polecenie zamknięcia wszystkich pozycji.",
    };
    try {
      await command.mutateAsync({ action, confirm });
      setMessage(success[action] || "Polecenie zostało wykonane.");
    } catch {
      setMessage("Nie udało się wykonać polecenia. Szczegóły zapisano w logach.");
    }
  };
  const connected = status.isSuccess && streamState !== "stale" && streamState !== "disconnected";
  return { data: snapshot, connected, message, setMessage, act, commandPending: command.isPending };
}
