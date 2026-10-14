import { useQuery, useQueryClient } from "@tanstack/react-query";
import { supabase } from "@/integrations/supabase/client";
import type { EventRow } from "@/lib/safenet";
import { canOperate, type AccessStatus } from '@/lib/access';
import { nowTime, toISODate } from '@/lib/safenet';

/** Two bounded reads per client; includes retained archives, never future entries. */
export async function readLastArmDisarm(clientIds: string[], before: Date): Promise<EventRow[]> {
  const date = toISODate(before);
  const time = `${nowTime(before)}:${String(before.getSeconds()).padStart(2, '0')}`;
  const rows = await Promise.all(clientIds.flatMap(clientId => (['arm', 'disarm'] as const).map(kind =>
    must<EventRow[]>(supabase.from('client_events').select('*').eq('client_id', clientId).eq('kind', kind)
      .or(`event_date.lt.${date},and(event_date.eq.${date},event_time.lt.${time})`)
      .order('event_date', { ascending: false }).order('event_time', { ascending: false, nullsFirst: false })
      .order('created_at', { ascending: false }).order('id', { ascending: false }).limit(1)),
  )));
  return rows.flat();
}

export function useClientArmStates(clientIds: string[]) {
  return useQuery({
    queryKey: ['events', 'arm-states', [...clientIds].sort()],
    enabled: clientIds.length > 0,
    refetchInterval: 15000,
    queryFn: () => readLastArmDisarm(clientIds, new Date(Date.now() + 1000)),
  });
}

export interface Profile { id: string; name: string; email: string | null }
export function useOperatorAccess() {
  return useQuery({ queryKey: ['operator-access'], refetchInterval: 15000, queryFn: async () => {
    const { data } = await supabase.auth.getUser();
    if (!data.user) return 'removed' as AccessStatus;
    const row = await must<{ status: AccessStatus }>(supabase.from('operator_access').select('status').eq('user_id', data.user.id).single());
    return row.status;
  } });
}
export interface Client { id: string; name: string; address: string | null; notes: string | null }
export interface Sensor { id: string; client_id: string; zone: string; name: string; type: string }
export interface Device { id: string; client_id: string; name: string; type: string }
export interface ClientUser { id: string; client_id: string; name: string; role: string | null }
export interface Bypass {
  id: string; client_id: string; sensor_id: string | null; sensor_label: string | null; reason: string | null;
  start_date: string; end_date: string | null; operator_id: string | null; archived: boolean; created_at: string;
}
export interface Shift {
  id: string; operator_id: string; shift_type: string; started_at: string;
  ended_at: string | null; next_operator_id: string | null; message: string | null;
}

async function must<T>(p: PromiseLike<{ data: unknown; error: { message: string } | null }>): Promise<T> {
  const { data, error } = await p;
  if (error) throw new Error(error.message);
  return data as T;
}

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      const { data } = await supabase.auth.getUser();
      if (!data.user) throw new Error('Entre novamente');
      const uid = data.user.id;
      const profile = await must<Profile>(supabase.from("profiles").select("id,name,email").eq("id", uid).single());
      const roles = await must<{ role: string }[]>(supabase.from("user_roles").select("role").eq("user_id", uid));
      const isAdmin = roles.some((r) => r.role === "admin");
      return { id: uid, profile, isAdmin, canManage: isAdmin || roles.some((r) => r.role === "manager") };
    },
  });
}

export const useOperators = (includeAll = false) =>
  useQuery({
    queryKey: ["operators", includeAll],
    queryFn: async () => {
      const profiles = await must<Profile[]>(supabase.from("profiles").select("id,name,email").order("name"));
      const roles = await must<{ user_id: string; role: string }[]>(supabase.from("user_roles").select("user_id,role"));
      const access = await must<{ user_id: string; status: AccessStatus }[]>(supabase.from('operator_access').select('user_id,status'));
      const has = (id: string, r: string) => roles.some((x) => x.user_id === id && x.role === r);
      return profiles.map((p) => ({ ...p, status: access.find(a => a.user_id === p.id)?.status ?? 'pending', isAdmin: has(p.id, "admin"), isManager: has(p.id, "manager") })).filter(p => includeAll || canOperate(p.status));
    },
  });

export const useClients = () =>
  useQuery({
    queryKey: ["clients"],
    queryFn: () => must<Client[]>(supabase.from("clients").select("*").eq("active", true).order("name")),
  });

export const useClientData = (clientId: string | undefined) =>
  useQuery({
    queryKey: ["client-data", clientId],
    enabled: !!clientId,
    queryFn: async () => {
      const [users, devices, sensors] = await Promise.all([
        must<ClientUser[]>(supabase.from("client_users").select("*").eq("client_id", clientId!).eq("active", true).order("name")),
        must<Device[]>(supabase.from("client_devices").select("*").eq("client_id", clientId!).eq("active", true).order("name")),
        must<Sensor[]>(supabase.from("client_sensors").select("*").eq("client_id", clientId!).eq("active", true).order("zone")),
      ]);
      return { users, devices, sensors };
    },
  });

export const useAllSensors = () =>
  useQuery({
    queryKey: ["all-sensors"],
    queryFn: () => must<Sensor[]>(supabase.from("client_sensors").select("*")),
  });

export interface CustomField {
  id: string; scope: string; label: string; field_type: string; options: string[]; position: number; active: boolean;
}
export const FIELD_SCOPES: Record<string, string> = {
  disarm: "Desarme", arm: "Arme", trigger: "Disparos", maintenance: "Manutenções", observation: "Observações",
};
export const useCustomFields = (scope?: string, includeInactive = false) =>
  useQuery({
    queryKey: ["custom-fields", scope, includeInactive],
    queryFn: () => {
      let q = supabase.from("custom_fields").select("*");
      if (scope) q = q.eq("scope", scope);
      if (!includeInactive) q = q.eq("active", true);
      return must<CustomField[]>(q.order("position").order("created_at"));
    },
  });

export const useEvents = (clientId: string | undefined, from: string, to: string, includeArchived = false) =>
  useQuery({
    queryKey: ["events", clientId, from, to, includeArchived],
    enabled: !!clientId,
    queryFn: () => {
      let q = supabase.from("client_events").select("*").eq("client_id", clientId!).gte("event_date", from).lte("event_date", to);
      if (!includeArchived) q = q.eq("archived", false);
      return must<EventRow[]>(q.order("event_date").order("event_time"));
    },
  });

/** Bypasses overlapping [from, to], or active ones if no range. */
export const useBypasses = (clientId: string | undefined, from?: string, to?: string, includeArchived = false) =>
  useQuery({
    queryKey: ["bypasses", clientId, from, to, includeArchived],
    enabled: !!clientId,
    queryFn: () => {
      let q = supabase.from("bypasses").select("*").eq("client_id", clientId!);
      if (from && to) q = q.lte("start_date", to).or(`end_date.is.null,end_date.gte.${from}`);
      else q = q.is("end_date", null);
      if (!includeArchived) q = q.eq("archived", false);
      return must<Bypass[]>(q.order("start_date"));
    },
  });

export const useActiveShift = () =>
  useQuery({
    queryKey: ["active-shift"],
    queryFn: () =>
      must<Shift[]>(supabase.from("shifts").select("*").is("ended_at", null).order("started_at", { ascending: false }).limit(1)).then(
        (r) => r[0] ?? null,
      ),
  });

export function useInvalidate() {
  const qc = useQueryClient();
  return (...keys: string[]) => keys.forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
}

export { must };

export const sensorLabel = (sensors: Sensor[] | undefined, id: string | null) => {
  const s = sensors?.find((x) => x.id === id);
  return s ? `Z${s.zone} ${s.name}` : "—";
};
