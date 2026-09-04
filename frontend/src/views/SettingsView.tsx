import { useEffect, useState, type FormEvent } from "react";

import {
  getBlofinCredentialsStatus,
  updateBlofinCredentials,
  type BlofinCredentialsStatus,
} from "../api";
import { Eye, EyeOff } from "lucide-react";
import { Card, ConfirmDialog, Metric, PageHeader, Pill, money } from "../components";
import type { Status } from "../types";

export interface SettingsViewProps {
  data: Status | null;
}

type CredentialAction = "save" | "test";

export function SettingsView({ data }: SettingsViewProps) {
  const [credentials, setCredentials] = useState<BlofinCredentialsStatus | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [secret, setSecret] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [confirmClear, setConfirmClear] = useState(false);
  const [revealed, setRevealed] = useState({ apiKey: false, secret: false, passphrase: false });

  useEffect(() => {
    const controller = new AbortController();
    getBlofinCredentialsStatus(controller.signal)
      .then(setCredentials)
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setMessage(error instanceof Error ? error.message : "Nie udało się odczytać konfiguracji API.");
      });
    return () => controller.abort();
  }, []);

  const submit = async (action: CredentialAction) => {
    setBusy(true);
    setMessage("");
    try {
      const next = await updateBlofinCredentials({
        action,
        api_key: apiKey,
        api_secret: secret,
        passphrase,
      });
      setCredentials(next);
      setApiKey("");
      setSecret("");
      setPassphrase("");
      setMessage(next.message || "Zapisano");
    } catch (error: unknown) {
      setMessage(error instanceof Error ? error.message : "Nie udało się zapisać konfiguracji API.");
    } finally {
      setBusy(false);
    }
  };

  const clear = async () => {
    setBusy(true);
    setMessage("");
    try {
      const next = await updateBlofinCredentials({ action: "clear", confirm: true });
      setCredentials(next);
      setConfirmClear(false);
      setMessage(next.message || "Usunięto klucze");
    } catch (error: unknown) {
      setMessage(error instanceof Error ? error.message : "Nie udało się usunąć kluczy API.");
    } finally {
      setBusy(false);
    }
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void submit("save");
  };

  const complete = Boolean(apiKey && secret && passphrase);

  return (
    <>
      <PageHeader
        title="Ustawienia i połączenie"
        description="Tryb konta, limity bezpieczeństwa i konfiguracja danych"
        context="ZAPIS LOKALNY"
      />
      <div className="settings-grid">
        <Card title="Tryb konta">
          <div className="mode-choice">
            <Pill tone={data?.engine.mode !== "LIVE" ? "good" : "muted"}>PAPER · {data?.engine.mode !== "LIVE" ? "AKTYWNY" : "NIEAKTYWNY"}</Pill>
            <Pill tone={data?.engine.mode === "LIVE" ? "bad" : "muted"}>LIVE · {data?.engine.mode === "LIVE" ? "AKTYWNY" : "ZABLOKOWANY"}</Pill>
          </div>
          <p className="muted-copy">
            Zmiana trybu wymaga zatrzymanego silnika. LIVE podlega osobnej blokadzie egzekucji.
          </p>
        </Card>

        <Card title="Limity ryzyka">
          <Metric label="Dzienny limit straty" value={`-${data?.session.daily_limit_pct ?? 5}%`} />
          <Metric label="Maks. pozycji" value={data?.session.max_positions ?? "—"} />
          <Metric
            label="Egzekucja LIVE"
            value={data?.engine.live_execution ? "WŁĄCZONA" : "ZABLOKOWANA"}
            tone={data?.engine.live_execution ? "bad" : "good"}
          />
        </Card>

        <Card title="BloFin API" className="credentials-card">
          <div className="credentials-head">
            <Pill tone={credentials?.configured ? "good" : credentials?.partial ? "warn" : "muted"}>
              {credentials?.configured
                ? `SKONFIGUROWANO · ${credentials.masked_key}`
                : credentials?.partial
                  ? "NIEKOMPLETNE"
                  : "BRAK KLUCZY"}
            </Pill>
            <span>Tylko lokalny, szyfrowany magazyn</span>
          </div>
          <form onSubmit={handleSubmit} autoComplete="off" noValidate>
            <label>
              API Key
              <span className="secret-field"><input
                type={revealed.apiKey ? "text" : "password"}
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                autoComplete="new-password"
                spellCheck={false}
                placeholder={credentials?.configured ? "Wpisz nowy, aby zastąpić zapisany" : "Wpisz API Key"}
              /><button type="button" onClick={() => setRevealed((value) => ({ ...value, apiKey: !value.apiKey }))} aria-label={revealed.apiKey ? "Ukryj API Key" : "Pokaż API Key"}>{revealed.apiKey ? <EyeOff/> : <Eye/>}</button></span>
            </label>
            <label>
              API Secret
              <span className="secret-field"><input
                type={revealed.secret ? "text" : "password"}
                value={secret}
                onChange={(event) => setSecret(event.target.value)}
                autoComplete="new-password"
                spellCheck={false}
                placeholder="Wpisz API Secret"
              /><button type="button" onClick={() => setRevealed((value) => ({ ...value, secret: !value.secret }))} aria-label={revealed.secret ? "Ukryj API Secret" : "Pokaż API Secret"}>{revealed.secret ? <EyeOff/> : <Eye/>}</button></span>
            </label>
            <label>
              Passphrase
              <span className="secret-field"><input
                type={revealed.passphrase ? "text" : "password"}
                value={passphrase}
                onChange={(event) => setPassphrase(event.target.value)}
                autoComplete="new-password"
                spellCheck={false}
                placeholder="Wpisz Passphrase"
              /><button type="button" onClick={() => setRevealed((value) => ({ ...value, passphrase: !value.passphrase }))} aria-label={revealed.passphrase ? "Ukryj Passphrase" : "Pokaż Passphrase"}>{revealed.passphrase ? <EyeOff/> : <Eye/>}</button></span>
            </label>
            <div className="credentials-actions">
              <button className="btn" type="submit" disabled={busy || !complete}>Zapisz klucze</button>
              <button className="btn good" type="button" disabled={busy || !complete} onClick={() => void submit("test")}>Zapisz i testuj</button>
              <button className="btn danger" type="button" disabled={busy || !credentials?.configured} onClick={() => setConfirmClear(true)}>Usuń zapisane</button>
            </div>
          </form>
          {message && <p className="credentials-message" role="status">{message}</p>}
          {credentials?.account && (
            <div className="credentials-account">
              <Metric label="Equity" value={`${money(credentials.account.equity)} ${credentials.account.currency}`} />
              <Metric label="Dostępne" value={`${money(credentials.account.available)} ${credentials.account.currency}`} />
              <Metric label="Otwarte pozycje" value={credentials.account.open_positions} />
            </div>
          )}
          <p className="muted-copy">
            Test wykonuje wyłącznie zapytania odczytu salda i pozycji. Nie przełącza LIVE i nie składa zleceń.
          </p>
        </Card>

        <Card title="Interfejs">
          <Metric label="Główny UI" value="React + Tauri" tone="info" />
          <Metric label="Awaryjny UI" value="PySide6" />
          <Metric label="Wersja silnika" value={`v${data?.version || "—"}`} />
        </Card>
      </div>
      <ConfirmDialog open={confirmClear} title="Usunąć zapisane klucze BloFin?" description="Klucze zostaną usunięte z lokalnego magazynu tego komputera. Bot pozostanie w trybie PAPER, a operacji nie można cofnąć." confirmLabel="Usuń klucze" busy={busy} onCancel={() => setConfirmClear(false)} onConfirm={() => void clear()}/>
    </>
  );
}
