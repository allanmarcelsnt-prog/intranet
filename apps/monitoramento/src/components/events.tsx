import { useState } from "react";
import { toast } from "sonner";
import { Trash2, Plus, ChevronDown } from "lucide-react";
import { supabase } from "@/integrations/supabase/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  useClientData, useCustomFields, useInvalidate, useMe, sensorLabel, type Bypass, type CustomField, type Sensor,
} from "@/hooks/use-safenet";
import { hhmm, nowTime, toISODate, formatBR, eventSensors, KIND_LABEL, type EventKind, type EventRow } from "@/lib/safenet";

export function useEventActions() {
  const inv = useInvalidate();
  const { data: me } = useMe();
  const refresh = () => inv("events", "bypasses");
  return {
    add: async (row: Partial<EventRow> & { client_id: string; kind: EventKind }) => {
      const { error } = await supabase.from("client_events").insert({ ...row, operator_id: me?.id ?? null } as never);
      if (error) { toast.error(error.message); return false; }
      refresh();
      return true;
    },
    update: async (id: string, patch: Partial<EventRow>) => {
      const { error } = await supabase.from("client_events").update(patch as never).eq("id", id);
      if (error) toast.error(error.message);
      refresh();
    },
    remove: async (id: string) => {
      if (!confirm("Excluir este registro?")) return;
      const { error } = await supabase.from("client_events").delete().eq("id", id);
      if (error) toast.error(error.message);
      refresh();
    },
  };
}

const sensorText = (s: Sensor) => `Z${s.zone} ${s.name}`;

/** Inputs for admin-defined extra fields of a section. */
function CustomInputs({ fields, values, onChange, compact }: { fields: CustomField[]; values: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void; compact?: boolean }) {
  if (!fields.length) return null;
  return (
    <>
      {fields.map((f) => {
        const v = String(values[f.id] ?? "");
        const set = (x: string) => onChange({ ...values, [f.id]: x });
        const input = f.field_type === "select" ? (
          <Select value={v} onValueChange={set}>
            <SelectTrigger className={compact ? "h-8 w-40" : ""}><SelectValue placeholder={f.label} /></SelectTrigger>
            <SelectContent>{f.options.map((o) => <SelectItem key={o} value={o}>{o}</SelectItem>)}</SelectContent>
          </Select>
        ) : (
          <Input className={compact ? "h-8 w-40" : ""} type={f.field_type === "number" ? "number" : "text"} placeholder={f.label} value={v} onChange={(e) => set(e.target.value)} />
        );
        return compact ? <div key={f.id}>{input}</div> : <div key={f.id} className="space-y-1"><Label>{f.label}</Label>{input}</div>;
      })}
    </>
  );
}

/** Arm / Disarm segment: date auto, time, user, device. */
export function ArmForm({ clientId, kind, date }: { clientId: string; kind: "arm" | "disarm"; date?: string }) {
  const { data } = useClientData(clientId);
  const { data: me } = useMe();
  const { data: fields = [] } = useCustomFields(kind);
  const { add } = useEventActions();
  const [time, setTime] = useState(nowTime());
  const [user, setUser] = useState("");
  const [device, setDevice] = useState("");
  const [custom, setCustom] = useState<Record<string, unknown>>({});
  const OP = "__operator__";
  const isArm = kind === "arm";

  const save = async () => {
    if (!time || !user || !device) { toast.error("Preencha horário, usuário e dispositivo"); return; }
    const byOp = user === OP;
    const ok = await add({
      client_id: clientId, kind, event_date: date ?? toISODate(new Date()), event_time: time,
      user_name: byOp ? me?.profile.name ?? "Operador" : user, device_name: device, by_operator: byOp, custom,
    });
    if (ok) { toast.success(`${isArm ? "Arme" : "Desarme"} registrado`); setTime(nowTime()); setCustom({}); }
  };

  return (
    <div className={`rounded-lg border-2 p-4 ${isArm ? "border-primary/40" : "border-warning/50"}`}>
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-bold uppercase tracking-wide">{isArm ? "Arme" : "Desarme"}</h3>
        <span className="font-mono text-sm text-muted-foreground">{formatBR(date ?? toISODate(new Date()))}</span>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1"><Label>Horário</Label>
          <Input type="time" className="font-mono" value={time} onChange={(e) => setTime(e.target.value)} /></div>
        <div className="space-y-1"><Label>Usuário</Label>
          <Select value={user} onValueChange={setUser}>
            <SelectTrigger><SelectValue placeholder="Quem realizou" /></SelectTrigger>
            <SelectContent>
              {data?.users.map((u) => <SelectItem key={u.id} value={u.name}>{u.name}</SelectItem>)}
              <SelectItem value={OP}>Operador ({me?.profile.name})</SelectItem>
            </SelectContent>
          </Select></div>
        <div className="space-y-1 sm:col-span-2"><Label>Dispositivo</Label>
          <Select value={device} onValueChange={setDevice}>
            <SelectTrigger><SelectValue placeholder="Dispositivo utilizado" /></SelectTrigger>
            <SelectContent>
              {data?.devices.map((d) => <SelectItem key={d.id} value={d.name}>{d.name} · {d.type}</SelectItem>)}
              <SelectItem value="Remoto (central de monitoramento)">Remoto (central de monitoramento)</SelectItem>
            </SelectContent>
          </Select></div>
        <CustomInputs fields={fields} values={custom} onChange={setCustom} />
      </div>
      <Button className="mt-3 w-full" variant={isArm ? "default" : "secondary"} onClick={save}>Registrar {isArm ? "arme" : "desarme"}</Button>
    </div>
  );
}

/** Multi-select of sensors. */
function SensorMulti({ sensors, value, onChange }: { sensors: Sensor[]; value: string[]; onChange: (v: string[]) => void }) {
  const label = value.length === 0 ? "Sensores / zonas" : sensors.filter((s) => value.includes(s.id)).map(sensorText).join(", ");
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" className="w-64 justify-between font-normal"><span className="truncate">{label}</span><ChevronDown className="h-4 w-4 opacity-60" /></Button>
      </PopoverTrigger>
      <PopoverContent className="w-72 p-2" align="start">
        {sensors.length === 0 && <p className="p-2 text-sm text-muted-foreground">Nenhum sensor cadastrado.</p>}
        <div className="max-h-64 space-y-1 overflow-y-auto">
          {sensors.map((s) => (
            <label key={s.id} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-accent">
              <Checkbox checked={value.includes(s.id)} onCheckedChange={(c) => onChange(c ? [...value, s.id] : value.filter((x) => x !== s.id))} />
              {sensorText(s)} <span className="text-xs text-muted-foreground">· {s.type}</span>
            </label>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}

/** Trigger / maintenance / observation quick add. */
export function QuickEventForm({ clientId, kind, date, pickDate }: { clientId: string; kind: "trigger" | "maintenance" | "observation"; date?: string; pickDate?: boolean }) {
  const { data } = useClientData(clientId);
  const { data: fields = [] } = useCustomFields(kind);
  const { add } = useEventActions();
  const [time, setTime] = useState(nowTime());
  const [endTime, setEndTime] = useState("");
  const [continuous, setContinuous] = useState(false);
  const [d, setD] = useState(date ?? toISODate(new Date()));
  const [sel, setSel] = useState<string[]>([]);
  const [desc, setDesc] = useState("");
  const [status, setStatus] = useState("Agendada");
  const [custom, setCustom] = useState<Record<string, unknown>>({});

  const save = async () => {
    if (kind === "trigger" && sel.length === 0) { toast.error("Selecione o(s) sensor(es) que dispararam"); return; }
    if (kind === "trigger" && continuous && !endTime) { toast.error("Informe o horário final do disparo contínuo"); return; }
    if (kind !== "trigger" && !desc.trim()) { toast.error("Descreva o registro"); return; }
    const chosen = (data?.sensors ?? []).filter((s) => sel.includes(s.id));
    const ok = await add({
      client_id: clientId, kind, event_date: date ?? d, event_time: kind === "observation" && !time ? null : time,
      end_time: kind === "trigger" && continuous ? endTime : null,
      sensor_id: sel[0] ?? null, sensor_ids: sel, sensor_labels: chosen.length ? chosen.map(sensorText).join(", ") : null,
      description: desc.trim().slice(0, 1000) || null, status: kind === "maintenance" ? status : null, custom,
    });
    if (ok) { setDesc(""); setSel([]); setTime(nowTime()); setEndTime(""); setCustom({}); toast.success("Registrado"); }
  };

  return (
    <div className="flex flex-wrap items-end gap-2">
      {!date && (pickDate || kind === "maintenance") && <Input type="date" className="w-40" value={d} onChange={(e) => setD(e.target.value)} />}
      <Input type="time" className="w-28 font-mono" value={time} onChange={(e) => setTime(e.target.value)} aria-label="Horário" />
      {kind === "trigger" && (
        <>
          <label className="flex items-center gap-1.5 text-xs"><Checkbox checked={continuous} onCheckedChange={(v) => setContinuous(!!v)} />contínuo</label>
          {continuous && <><span className="text-sm text-muted-foreground">às</span><Input type="time" className="w-28 font-mono" value={endTime} onChange={(e) => setEndTime(e.target.value)} aria-label="Horário final" /></>}
          <SensorMulti sensors={data?.sensors ?? []} value={sel} onChange={setSel} />
        </>
      )}
      {kind === "maintenance" && (
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
          <SelectContent><SelectItem value="Agendada">Agendada</SelectItem><SelectItem value="Realizada">Realizada</SelectItem></SelectContent>
        </Select>
      )}
      <Input className="min-w-48 flex-1" maxLength={1000} placeholder={kind === "trigger" ? "Detalhe (opcional)" : "Descrição"} value={desc} onChange={(e) => setDesc(e.target.value)} />
      <CustomInputs fields={fields} values={custom} onChange={setCustom} compact />
      <Button size="icon" onClick={save} aria-label="Adicionar"><Plus className="h-4 w-4" /></Button>
    </div>
  );
}

/** Editable list of events (inline edit on blur). */
export function EventList({ events, sensors, showDate }: { events: EventRow[]; sensors?: Sensor[] | undefined; showDate?: boolean }) {
  const { update, remove } = useEventActions();
  const { data: me } = useMe();
  const { data: fields = [] } = useCustomFields(undefined, true);
  if (!events.length) return <p className="py-2 text-sm text-muted-foreground">Nenhum registro.</p>;
  return (
    <ul className="divide-y">
      {events.map((e) => {
        const isArmKind = e.kind === "arm" || e.kind === "disarm";
        const extra = fields.filter((f) => f.scope === e.kind && e.custom?.[f.id] !== undefined && e.custom[f.id] !== "");
        return (
          <li key={e.id} className={`flex flex-wrap items-center gap-2 py-1.5 text-sm ${e.archived ? "opacity-50" : ""}`}>
            {isArmKind && (
              <span className={`w-20 rounded px-2 py-0.5 text-center text-xs font-bold uppercase ${e.kind === "disarm" ? "bg-warning/25" : "bg-primary text-primary-foreground"}`}>{KIND_LABEL[e.kind]}</span>
            )}
            {showDate && <Input type="date" className="h-8 w-36" defaultValue={e.event_date} onBlur={(x) => x.target.value !== e.event_date && update(e.id, { event_date: x.target.value })} />}
            <Input type="time" className="h-8 w-24 font-mono" defaultValue={hhmm(e.event_time)} onBlur={(x) => x.target.value !== hhmm(e.event_time) && update(e.id, { event_time: x.target.value || null })} />
            {e.kind === "trigger" && e.end_time && (
              <><span className="text-xs text-muted-foreground">às</span><Input type="time" className="h-8 w-24 font-mono" defaultValue={hhmm(e.end_time)} onBlur={(x) => x.target.value !== hhmm(e.end_time) && update(e.id, { end_time: x.target.value || null })} /></>
            )}
            {e.kind === "trigger" && <span className="rounded bg-destructive/15 px-2 py-0.5 text-xs font-semibold text-destructive">{eventSensors(e, (id) => sensorLabel(sensors, id)) || "—"}</span>}
            {isArmKind && (
              <>
                <Input className="h-8 w-36" defaultValue={e.user_name ?? ""} onBlur={(x) => update(e.id, { user_name: x.target.value })} aria-label="Usuário" />
                <Input className="h-8 w-40" defaultValue={e.device_name ?? ""} onBlur={(x) => update(e.id, { device_name: x.target.value })} aria-label="Dispositivo" />
                {e.by_operator && <span className="rounded bg-accent px-1.5 text-xs">operador</span>}
              </>
            )}
            {e.kind === "maintenance" && (
              <Select value={e.status ?? "Agendada"} onValueChange={(v) => update(e.id, { status: v })}>
                <SelectTrigger className="h-8 w-28"><SelectValue /></SelectTrigger>
                <SelectContent><SelectItem value="Agendada">Agendada</SelectItem><SelectItem value="Realizada">Realizada</SelectItem></SelectContent>
              </Select>
            )}
            {!isArmKind && (
              <Input className="h-8 min-w-40 flex-1" defaultValue={e.description ?? ""} onBlur={(x) => x.target.value !== (e.description ?? "") && update(e.id, { description: x.target.value })} />
            )}
            {extra.map((f) => <span key={f.id} className="rounded bg-muted px-2 py-0.5 text-xs"><span className="text-muted-foreground">{f.label}:</span> {String(e.custom[f.id])}</span>)}
            {me?.isAdmin && <Button size="icon" variant="ghost" className="ml-auto h-8 w-8" onClick={() => remove(e.id)} aria-label="Excluir"><Trash2 className="h-4 w-4" /></Button>}
          </li>
        );
      })}
    </ul>
  );
}

/** Zones currently/within-range inhibited, with add and release. */
export function BypassPanel({ clientId, bypasses, sensors, defaultDate }: { clientId: string; bypasses: Bypass[]; sensors?: Sensor[] | undefined; defaultDate?: string }) {
  const inv = useInvalidate();
  const { data: me } = useMe();
  const [sensor, setSensor] = useState("");
  const [reason, setReason] = useState("");
  const [startD, setStartD] = useState(defaultDate ?? toISODate(new Date()));
  const [ongoing, setOngoing] = useState(true);

  const add = async () => {
    if (!sensor) { toast.error("Selecione o sensor"); return; }
    const s = sensors?.find((x) => x.id === sensor);
    const { error } = await supabase.from("bypasses").insert({ client_id: clientId, sensor_id: sensor, sensor_label: s ? sensorText(s) : null, reason: reason.trim().slice(0, 500) || null, start_date: startD, end_date: ongoing ? null : startD, operator_id: me?.id ?? null });
    if (error) { toast.error(error.message); return; }
    setSensor(""); setReason(""); inv("bypasses");
  };
  const patch = async (id: string, p: Partial<Bypass>) => {
    const { error } = await supabase.from("bypasses").update(p).eq("id", id);
    if (error) toast.error(error.message);
    inv("bypasses");
  };
  const del = async (id: string) => {
    if (!confirm("Excluir este registro?")) return;
    const { error } = await supabase.from("bypasses").delete().eq("id", id);
    if (error) toast.error(error.message);
    inv("bypasses");
  };

  return (
    <div className="space-y-2">
      {bypasses.length === 0 && <p className="text-sm text-muted-foreground">Nenhuma zona inibida.</p>}
      <ul className="divide-y">
        {bypasses.map((b) => (
          <li key={b.id} className="flex flex-wrap items-center gap-2 py-1.5 text-sm">
            <span className={`rounded px-2 py-0.5 text-xs font-semibold ${b.end_date ? "bg-muted text-muted-foreground" : "bg-warning/25"}`}>{b.sensor_label ?? sensorLabel(sensors, b.sensor_id)}</span>
            <Input className="h-8 min-w-40 flex-1" defaultValue={b.reason ?? ""} placeholder="Motivo" onBlur={(x) => patch(b.id, { reason: x.target.value })} />
            <span className="font-mono text-xs text-muted-foreground">{formatBR(b.start_date)} → {b.end_date ? formatBR(b.end_date) : "ativa"}</span>
            {!b.end_date && <Button size="sm" variant="outline" className="h-8" onClick={() => patch(b.id, { end_date: toISODate(new Date()) })}>Liberar</Button>}
            {me?.isAdmin && <Button size="icon" variant="ghost" className="h-8 w-8" onClick={() => del(b.id)} aria-label="Excluir"><Trash2 className="h-4 w-4" /></Button>}
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-end gap-2 border-t pt-2">
        <Select value={sensor} onValueChange={setSensor}>
          <SelectTrigger className="w-56"><SelectValue placeholder="Inibir sensor / zona" /></SelectTrigger>
          <SelectContent>{sensors?.map((s) => <SelectItem key={s.id} value={s.id}>Z{s.zone} {s.name} · {s.type}</SelectItem>)}</SelectContent>
        </Select>
        <Input type="date" className="w-40" value={startD} onChange={(e) => setStartD(e.target.value)} />
        <Input className="min-w-40 flex-1" placeholder="Motivo" value={reason} onChange={(e) => setReason(e.target.value)} />
        <label className="flex items-center gap-1.5 text-xs"><Checkbox checked={ongoing} onCheckedChange={(v) => setOngoing(!!v)} /> segue inibida</label>
        <Button size="icon" onClick={add} aria-label="Adicionar"><Plus className="h-4 w-4" /></Button>
      </div>
    </div>
  );
}
