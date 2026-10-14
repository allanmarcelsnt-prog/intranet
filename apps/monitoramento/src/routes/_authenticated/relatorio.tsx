import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, FileDown, Printer, FolderOpen } from "lucide-react";
import { toast } from "sonner";
import { supabase } from "@/integrations/supabase/client";
import { useBypasses, useClientData, useClients, useEvents, useMe, useInvalidate, useCustomFields, must, sensorLabel, type Bypass } from "@/hooks/use-safenet";
import { chooseExportFolder, saveExport } from '@/lib/export-files';
import logoUrl from '@/assets/logo-safenet.png?url';
import { archiveDeadline } from '@/lib/archive-retention';
import type { Json } from '@/integrations/supabase/types';
import { ArmForm, BypassPanel, EventList, QuickEventForm } from "@/components/events";
import { AcervoPanel } from "@/components/acervo";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  adjacentReportWeek, eventSensors, formatBR, hhmm, KIND_LABEL, monthRange, toCSV, weekDays, weekdayLabel, type EventRow,
} from "@/lib/safenet";

export const Route = createFileRoute("/_authenticated/relatorio")({
  head: () => ({
    meta: [
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
      { title: "Relatório semanal — Monitoramento Safenet" },
      { name: "description", content: "Relatório semanal por cliente: armes, desarmes, disparos, zonas inibidas, manutenções e observações." },
      { property: "og:title", content: "Relatório semanal — Monitoramento Safenet" },
      { property: "og:description", content: "Consulta e edição semanal consolidada por cliente." },
    ],
  }),
  component: Relatorio,
});

function Relatorio() {
  const { data: clients = [] } = useClients();
  const { data: me } = useMe();
  const { data: fields = [] } = useCustomFields(undefined, true);
  const [clientId, setClientId] = useState<string>();
  const [ref, setRef] = useState(() => new Date());
  const [showArchived, setShowArchived] = useState(false);
  const [addDay, setAddDay] = useState<string | null>(null);
  const [folderName, setFolderName] = useState('');
  const [busy, setBusy] = useState(false);
  const [pendingArchive, setPendingArchive] = useState<Json | null>(null);
  const inv = useInvalidate();
  const chooseFolder = async () => {
    try { setFolderName(await chooseExportFolder()); }
    catch (e) { if (e instanceof Error && e.name !== 'AbortError') toast.error(e.message); }
  };
  const exportPDF = async () => {
    if (!client || !clientId) return;
    try {
      const [{ jsPDF }, { buildReport, renderReportPdf }, acervo, logo] = await Promise.all([
        import('jspdf'),
        import('@/lib/relatorio-pdf'),
        must<EventRow[]>(supabase.from('client_events').select('*').eq('client_id', clientId).eq('kind', 'observation')),
        fetch(logoUrl).then((r) => r.blob()).then((b) => new Promise<string>((ok, fail) => {
          const fr = new FileReader(); fr.onload = () => ok(String(fr.result)); fr.onerror = () => fail(fr.error); fr.readAsDataURL(b);
        })).catch(() => null),
      ]);
      const report = buildReport({
        client: { name: client.name, address: client.address, notes: client.notes }, days, events, bypasses, acervo,
        sensorName: (id) => sensorLabel(data?.sensors, id),
      });
      const pdf = new jsPDF({ unit: 'mm', format: 'a4' });
      renderReportPdf(pdf, report, logo);
      await saveExport(`relatorio_${client.name.replace(/\W+/g, '_')}_${from}.pdf`, pdf.output('blob'));
    } catch (e) { if (e instanceof Error && e.name !== 'AbortError') toast.error(e.message); }
  };
  useEffect(() => { if (!clientId && clients[0]) setClientId(clients[0].id); }, [clients, clientId]);

  const days = weekDays(ref);
  const from = days[0] ?? '', to = days.at(-1) ?? '';
  const { data } = useClientData(clientId);
  const { data: events = [] } = useEvents(clientId, from, to, showArchived);
  const { data: bypasses = [] } = useBypasses(clientId, from, to, showArchived);
  const client = clients.find((c) => c.id === clientId);
  const by = (k: EventRow["kind"]) => events.filter((e) => e.kind === k);

  const exportCSV = async (scope: "week" | "month") => {
    if (!clientId || !client) return;
    const r = scope === "week" ? { from, to } : monthRange(ref);
    const [ev, bp] = await Promise.all([
      must<EventRow[]>(supabase.from("client_events").select("*").eq("client_id", clientId).gte("event_date", r.from).lte("event_date", r.to).order("event_date").order("event_time")),
      must<Bypass[]>(supabase.from("bypasses").select("*").eq("client_id", clientId).lte("start_date", r.to).or(`end_date.is.null,end_date.gte.${r.from}`)),
    ]);
    const rows = [
      ...ev.map((e) => ({
        record_id: e.id, client_id: e.client_id, client_name: client.name, record_type: e.kind, event_date: e.event_date,
        event_time: hhmm(e.event_time), event_end_time: hhmm(e.end_time), user_name: e.user_name, device_name: e.device_name, by_operator: e.by_operator,
        sensor: eventSensors(e, (id) => sensorLabel(data?.sensors, id)), description: e.description, status: e.status,
        end_date: "", custom: Object.keys(e.custom ?? {}).length ? JSON.stringify(e.custom) : "", archived: e.archived, created_at: e.created_at,
      })),
      ...bp.map((b) => ({
        record_id: b.id, client_id: b.client_id, client_name: client.name, record_type: "bypass", event_date: b.start_date,
        event_time: "", event_end_time: "", user_name: "", device_name: "", by_operator: false, sensor: b.sensor_label ?? sensorLabel(data?.sensors, b.sensor_id),
        description: b.reason, status: b.end_date ? "liberada" : "ativa", end_date: b.end_date ?? "", custom: "", archived: b.archived, created_at: b.created_at,
      })),
    ];
    const cols = ["record_id", "client_id", "client_name", "record_type", "event_date", "event_time", "event_end_time", "user_name", "device_name", "by_operator", "sensor", "description", "status", "end_date", "custom", "archived", "created_at"];
    await saveExport(`safenet_${client.name.replace(/\W+/g, "_")}_${r.from}_${r.to}.csv`, new Blob(['\ufeff', toCSV(rows, cols)], { type: 'text/csv;charset=utf-8' }));
  };

  const confirmArchive = async (snapshot: Json) => {
    const { data: count, error } = await supabase.rpc('confirm_saved_archive', { _snapshot: snapshot });
    if (error) throw new Error(error.message);
    setPendingArchive(null); inv('events', 'bypasses');
    toast.success(`${count} registros arquivados; exclusão após ${formatBR(archiveDeadline(new Date()).toISOString().slice(0, 10))}.`);
  };
  const saveArchive = async () => {
    setBusy(true);
    try {
      const { data: snapshot, error } = await supabase.rpc('prepare_archive_month', { _month: monthRange(ref).from });
      if (error || !snapshot) throw new Error(error?.message ?? 'Não foi possível preparar a cópia');
      const result = await saveExport(`safenet_arquivo_${monthRange(ref).from}_${Date.now()}.json`, new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json' }));
      if (result === 'saved') await confirmArchive(snapshot);
      else setPendingArchive(snapshot);
    } catch (e) { if (e instanceof Error && e.name !== 'AbortError') toast.error(e.message); }
    finally { setBusy(false); }
  };

  const archive = async (undo: boolean) => {
    const m = monthRange(ref).from;
    const { data: n, error } = await supabase.rpc("archive_month", { _month: m, _archived: !undo });
    if (error) { toast.error(error.message); return; }
    toast.success(`${n} registros ${undo ? "restaurados" : "arquivados"} (${m.slice(5, 7)}/${m.slice(0, 4)})`);
    inv('events', 'bypasses');
  };

  return (
    <div className="space-y-4">
      <div className="no-print flex flex-wrap items-center gap-2">
        <h1 className="mr-auto text-2xl font-bold">Relatório Semanal</h1>
        <Select value={clientId ?? ""} onValueChange={setClientId}>
          <SelectTrigger className="w-56"><SelectValue placeholder="Cliente" /></SelectTrigger>
          <SelectContent>{clients.map((c) => <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>)}</SelectContent>
        </Select>
        <div className="flex items-center rounded-md border bg-card">
          <Button variant="ghost" size="icon" onClick={() => setRef(adjacentReportWeek(ref, -1))} aria-label="Semana anterior"><ChevronLeft className="h-4 w-4" /></Button>
          <span className="px-2 font-mono text-sm">{formatBR(from)} – {formatBR(to)}</span>
          <Button variant="ghost" size="icon" onClick={() => setRef(adjacentReportWeek(ref, 1))} aria-label="Próxima semana"><ChevronRight className="h-4 w-4" /></Button>
        </div>
        <Button variant="outline" size="sm" onClick={() => setRef(new Date())}>Semana atual</Button>
      </div>
      <div className="no-print flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" onClick={() => exportCSV("week").catch(e => { if (e.name !== 'AbortError') toast.error(e.message); })}><FileDown className="mr-1 h-4 w-4" />CSV semana</Button>
        <Button size="sm" variant="secondary" onClick={() => exportCSV("month").catch(e => { if (e.name !== 'AbortError') toast.error(e.message); })}><FileDown className="mr-1 h-4 w-4" />CSV mês</Button>
        <Button size="sm" variant="secondary" onClick={exportPDF}><Printer className="mr-1 h-4 w-4" />PDF semana</Button>
        {me?.isAdmin && (
          <>
            <Button size="sm" variant="outline" onClick={chooseFolder}><FolderOpen className="mr-1 h-4 w-4" />{folderName || 'Escolher pasta'}</Button>
            <label className="ml-auto flex items-center gap-2 text-sm"><Switch checked={showArchived} onCheckedChange={setShowArchived} />Mostrar arquivados</label>
            <Button size="sm" variant="outline" disabled={busy || !!pendingArchive} onClick={saveArchive}>Salvar e arquivar mês de {formatBR(from).slice(3)}</Button>
            <Button size="sm" variant="ghost" onClick={() => archive(true)}>Restaurar mês</Button>
          </>
        )}
      </div>

      <Dialog open={pendingArchive !== null} onOpenChange={o => { if (!o) setPendingArchive(null); }}>
        <DialogContent><DialogHeader><DialogTitle>Confirmar cópia salva</DialogTitle></DialogHeader>
          <p>Confirme que o arquivo JSON foi salvo e pode ser aberto. Os registros arquivados serão excluídos do sistema após um mês; o arquivo salvo permanece na pasta.</p>
          <Button variant="destructive" disabled={busy} onClick={async () => { if (!pendingArchive) return; setBusy(true); try { await confirmArchive(pendingArchive); } catch(e) { toast.error(e instanceof Error ? e.message : 'Erro ao arquivar'); } finally { setBusy(false); } }}>Cópia salva — arquivar</Button>
          <Button variant="outline" onClick={() => setPendingArchive(null)}>Cancelar</Button>
        </DialogContent>
      </Dialog>

      {!client ? <p className="text-muted-foreground">Cadastre um cliente para ver o relatório.</p> : (
        <>
          <div className="hidden print:block">
            <h1 className="text-2xl font-bold">Safenet — Relatório Semanal</h1>
            <p>{client.name} · {formatBR(from)} a {formatBR(to)}</p>
          </div>
          <Card>
            <CardHeader className="pb-2"><CardTitle className="text-base">Armes e desarmes · {client.name}</CardTitle></CardHeader>
            <CardContent className="overflow-x-auto">
              <div className="grid min-w-[840px] grid-cols-7 gap-2">
                {days.map((d) => {
                  const dayEv = events.filter((e) => e.event_date === d && (e.kind === "arm" || e.kind === "disarm"));
                  return (
                    <div key={d} className="rounded-md border bg-muted/40 p-2">
                      <div className="mb-2 flex items-baseline justify-between">
                        <span className="text-xs font-bold uppercase">{weekdayLabel(d)}</span>
                        <span className="font-mono text-xs text-muted-foreground">{formatBR(d).slice(0, 5)}</span>
                      </div>
                      {(["disarm", "arm"] as const).map((k) => (
                        <div key={k} className="mb-2">
                          <div className="text-[10px] font-semibold uppercase text-muted-foreground">{KIND_LABEL[k]}</div>
                          {dayEv.filter((e) => e.kind === k).map((e) => (
                            <div key={e.id} className="text-xs"><span className="font-mono font-bold">{hhmm(e.event_time)}</span> {e.user_name}<div className="truncate text-[10px] text-muted-foreground">{e.device_name}</div></div>
                          ))}
                          {!dayEv.some((e) => e.kind === k) && <div className="text-xs text-muted-foreground">—</div>}
                        </div>
                      ))}
                      <button className="no-print text-xs text-primary hover:underline" onClick={() => setAddDay(d)}>+ adicionar</button>
                    </div>
                  );
                })}
              </div>
              <details className="no-print mt-3">
                <summary className="cursor-pointer text-sm text-muted-foreground">Editar armes/desarmes da semana</summary>
                <EventList events={[...by("disarm"), ...by("arm")].sort((a, b) => (a.event_date + a.event_time < b.event_date + b.event_time ? -1 : 1))} showDate />
              </details>
            </CardContent>
          </Card>
          <Section title="Disparos de alarme"><div className="no-print"><QuickEventForm clientId={client.id} kind="trigger" pickDate /></div><EventList events={by("trigger")} sensors={data?.sensors} showDate /></Section>
          <Section title="Zonas inibidas"><BypassPanel clientId={client.id} bypasses={bypasses} sensors={data?.sensors} defaultDate={from} /></Section>
          <Section title="Manutenções"><div className="no-print"><QuickEventForm clientId={client.id} kind="maintenance" /></div><EventList events={by("maintenance")} showDate /></Section>
          <Section title="Pontos de atenção e anormalidades técnicas"><AcervoPanel clientId={client.id} /></Section>
          <Section title="Observações"><div className="no-print"><QuickEventForm clientId={client.id} kind="observation" pickDate /></div><EventList events={by("observation")} showDate /></Section>
        </>
      )}

      <Dialog open={!!addDay} onOpenChange={(o) => !o && setAddDay(null)}>
        <DialogContent className="max-w-3xl">
          <DialogHeader><DialogTitle>Registrar em {addDay && formatBR(addDay)}</DialogTitle></DialogHeader>
          {addDay && clientId && <div className="grid gap-3 md:grid-cols-2"><ArmForm clientId={clientId} kind="disarm" date={addDay} /><ArmForm clientId={clientId} kind="arm" date={addDay} /></div>}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <Card><CardHeader className="pb-2"><CardTitle className="text-base">{title}</CardTitle></CardHeader><CardContent className="space-y-2">{children}</CardContent></Card>;
}
