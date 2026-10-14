import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Copy, Flag, Play, Send, ShieldAlert, ShieldOff } from "lucide-react";
import { supabase } from "@/integrations/supabase/client";
import {
  useActiveShift, useAllSensors, useBypasses, useClientData, useClients, useEvents, useInvalidate, useMe, useOperators,
  must, sensorLabel, type Bypass, useClientArmStates, readLastArmDisarm,
} from "@/hooks/use-safenet";
import { ArmForm, BypassPanel, EventList, QuickEventForm } from "@/components/events";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { buildShiftMessage, nextShiftType, toISODate, isDisarmed, shiftContainsEvent, type EventRow } from "@/lib/safenet";

export const Route = createFileRoute("/_authenticated/plantao")({
  head: () => ({
    meta: [
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
      { title: "Plantão — Monitoramento Safenet" },
      { name: "description", content: "Registro em tempo real de armes, desarmes, disparos e zonas inibidas durante o turno." },
      { property: "og:title", content: "Plantão — Monitoramento Safenet" },
      { property: "og:description", content: "Acompanhamento de plantão da equipe de monitoramento." },
    ],
  }),
  component: Plantao,
});

function Plantao() {
  const { data: clients = [] } = useClients();
  const { data: armStates = [], isError: stateError } = useClientArmStates(clients.map(c => c.id));
  const { data: shift } = useActiveShift();
  const [clientId, setClientId] = useState<string>();
  const [endOpen, setEndOpen] = useState(false);
  useEffect(() => { if (!clientId && clients[0]) setClientId(clients[0].id); }, [clients, clientId]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Acompanhamento de Plantão</h1>
        {shift ? (
          <Button variant="destructive" size="lg" onClick={() => setEndOpen(true)}><Flag className="mr-2 h-4 w-4" />Fim de Plantão</Button>
        ) : <StartShift />}
      </div>
      {clients.length === 0 ? (
        <Card><CardContent className="p-6 text-muted-foreground">Cadastre clientes na aba Clientes para começar.</CardContent></Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[220px_1fr]">
          <div className="flex gap-2 overflow-x-auto lg:flex-col">
            {clients.map((c) => (
              <Button key={c.id} onClick={() => setClientId(c.id)} variant={c.id === clientId ? "default" : "outline"}
                className="h-auto shrink-0 justify-start gap-2 whitespace-normal px-3 py-2.5 text-left text-sm"
                title={stateError ? 'Não foi possível consultar o estado registrado' : undefined}>
                {isDisarmed(armStates.filter(e => e.client_id === c.id)) && <ShieldOff className="h-4 w-4 shrink-0" aria-label="Desarmado conforme último registro" role="img"><title>Desarmado conforme último registro</title></ShieldOff>}
                <span className="min-w-0 break-words">{c.name}</span>
              </Button>
            ))}
          </div>
          {clientId && <ClientPanel clientId={clientId} />}
        </div>
      )}
      {shift && <EndShiftDialog open={endOpen} onOpenChange={setEndOpen} />}
    </div>
  );
}

function StartShift() {
  const { data: me } = useMe();
  const inv = useInvalidate();
  const [type, setType] = useState(new Date().getHours() >= 6 && new Date().getHours() < 18 ? "Diurno" : "Noturno");
  const start = async () => {
    if (!me) return;
    const { error } = await supabase.from("shifts").insert({ operator_id: me.id, shift_type: type });
    if (error) { toast.error(error.message); return; }
    toast.success("Plantão iniciado"); inv("active-shift");
  };
  return (
    <div className="flex gap-2">
      <Select value={type} onValueChange={setType}>
        <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
        <SelectContent><SelectItem value="Diurno">Diurno</SelectItem><SelectItem value="Noturno">Noturno</SelectItem></SelectContent>
      </Select>
      <Button size="lg" onClick={start}><Play className="mr-2 h-4 w-4" />Iniciar plantão</Button>
    </div>
  );
}

function ClientPanel({ clientId }: { clientId: string }) {
  const today = toISODate(new Date());
  const { data } = useClientData(clientId);
  const { data: events = [] } = useEvents(clientId, today, today);
  const { data: active = [] } = useBypasses(clientId);
  const by = (k: EventRow["kind"]) => events.filter((e) => e.kind === k);

  return (
    <div className="space-y-4">
      <Card className="border-warning/50">
        <CardHeader className="pb-2"><CardTitle className="flex items-center gap-2 text-base"><ShieldAlert className="h-4 w-4" />Zonas inibidas agora ({active.length})</CardTitle></CardHeader>
        <CardContent><BypassPanel clientId={clientId} bypasses={active} sensors={data?.sensors} /></CardContent>
      </Card>
      <div className="grid gap-4 md:grid-cols-2">
        <ArmForm clientId={clientId} kind="disarm" />
        <ArmForm clientId={clientId} kind="arm" />
      </div>
      <Card>
        <CardHeader className="pb-2"><CardTitle className="text-base">Armes e desarmes de hoje</CardTitle></CardHeader>
        <CardContent><EventList events={[...by("disarm"), ...by("arm")].sort((a, b) => (a.event_time ?? "") < (b.event_time ?? "") ? -1 : 1)} /></CardContent>
      </Card>
      {(["trigger", "maintenance", "observation"] as const).map((k) => (
        <Card key={k}>
          <CardHeader className="pb-2"><CardTitle className="text-base">{{ trigger: "Disparos", maintenance: "Manutenções", observation: "Observações" }[k]}</CardTitle></CardHeader>
          <CardContent className="space-y-2"><QuickEventForm clientId={clientId} kind={k} /><EventList events={by(k)} sensors={data?.sensors} /></CardContent>
        </Card>
      ))}
    </div>
  );
}

function EndShiftDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { data: shift } = useActiveShift();
  const { data: ops = [] } = useOperators(true);
  const { data: clients = [] } = useClients();
  const { data: sensors } = useAllSensors();
  const inv = useInvalidate();
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState("");
  const opName = useMemo(() => ops.find((o) => o.id === shift?.operator_id)?.name ?? "", [ops, shift]);

  useEffect(() => { if (!open) { setMsg(""); setNext(""); } }, [open]);

  const generate = async () => {
    if (!shift || !next) { toast.error("Selecione o próximo operador"); return; }
    const startDate = toISODate(new Date(shift.started_at));
    const today = toISODate(new Date());
    const generatedAt = new Date();
    const [events, bps, previousEvents] = await Promise.all([
      must<EventRow[]>(supabase.from("client_events").select("*").gte("event_date", startDate).lte("event_date", today).eq("archived", false)),
      must<Bypass[]>(supabase.from("bypasses").select("*").gte("created_at", shift.started_at)),
      readLastArmDisarm(clients.map(c => c.id), new Date(shift.started_at)),
    ]);
    const inShift = (e: EventRow) => shiftContainsEvent(e, shift.started_at, generatedAt);
    const text = buildShiftMessage(
      clients.map((c) => ({
        clientName: c.name,
        date: today,
        events: events.filter((e) => e.client_id === c.id && inShift(e)),
        previousEvents: previousEvents.filter(e => e.client_id === c.id),
        newBypasses: bps.filter((b) => b.client_id === c.id).map((b) => ({ sensorLabel: b.sensor_label ?? sensorLabel(sensors, b.sensor_id), reason: b.reason })),
        sensorLabel: (id) => sensorLabel(sensors, id),
      })),
      { shiftType: shift.shift_type, operatorName: opName, nextShiftType: nextShiftType(shift.shift_type), nextOperatorName: ops.find((o) => o.id === next)?.name ?? "" },
    );
    setMsg(text);
  };

  const finish = async () => {
    if (!shift) return;
    const { error } = await supabase.from("shifts").update({ ended_at: new Date().toISOString(), next_operator_id: next, message: msg }).eq("id", shift.id);
    if (error) { toast.error(error.message); return; }
    await supabase.from("shifts").insert({ operator_id: next, shift_type: nextShiftType(shift.shift_type) });
    await navigator.clipboard.writeText(msg).catch(() => {});
    toast.success("Plantão encerrado e mensagem copiada");
    inv("active-shift"); onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader><DialogTitle>Fim de Plantão — {opName} ({shift?.shift_type})</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div className="flex items-end gap-2">
            <div className="flex-1 space-y-1"><Label>Próximo operador</Label>
              <Select value={next} onValueChange={setNext}>
                <SelectTrigger><SelectValue placeholder="Selecione" /></SelectTrigger>
                <SelectContent>{ops.filter(o => o.status === 'approved').map((o) => <SelectItem key={o.id} value={o.id}>{o.name}</SelectItem>)}</SelectContent>
              </Select></div>
            <Button onClick={generate}>Gerar relatório</Button>
          </div>
          {msg && <Textarea className="h-80 font-mono text-xs" value={msg} onChange={(e) => setMsg(e.target.value)} />}
        </div>
        {msg && (
          <DialogFooter className="flex-wrap gap-2">
            <Button variant="outline" onClick={() => navigator.clipboard.writeText(msg).then(() => toast.success("Copiado"))}><Copy className="mr-2 h-4 w-4" />Copiar</Button>
            <Button variant="outline" asChild><a href={`https://wa.me/?text=${encodeURIComponent(msg)}`} target="_blank" rel="noreferrer"><Send className="mr-2 h-4 w-4" />WhatsApp</a></Button>
            <Button variant="destructive" onClick={finish}>Encerrar plantão</Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}
