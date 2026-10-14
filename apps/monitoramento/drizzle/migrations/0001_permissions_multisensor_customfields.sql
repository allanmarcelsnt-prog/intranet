alter type public.app_role add value if not exists 'manager';

create or replace function public.can_manage(_uid uuid) returns boolean language sql stable security definer set search_path = public
as $$ select exists (select 1 from public.user_roles where user_id = _uid and role::text in ('admin','manager')) $$;

-- events: multiple sensors, interval, name snapshots, custom values
alter table public.client_events add column sensor_ids uuid[] not null default '{}';
alter table public.client_events add column sensor_labels text;
alter table public.client_events add column end_time time;
alter table public.client_events add column custom jsonb not null default '{}'::jsonb;
alter table public.bypasses add column sensor_label text;
update public.client_events set sensor_ids = array[sensor_id] where sensor_id is not null;

-- soft delete for registrations (history preserved)
alter table public.clients add column active boolean not null default true;
alter table public.client_users add column active boolean not null default true;
alter table public.client_devices add column active boolean not null default true;
alter table public.client_sensors add column active boolean not null default true;

create table public.custom_fields (
  id uuid primary key default gen_random_uuid(),
  scope text not null,
  label text not null,
  field_type text not null default 'text',
  options text[] not null default '{}',
  position int not null default 0,
  active boolean not null default true,
  created_at timestamptz not null default now()
);
grant select, insert, update, delete on public.custom_fields to authenticated;
grant all on public.custom_fields to service_role;
alter table public.custom_fields enable row level security;

do $$ declare t text; begin
  foreach t in array array['clients','client_users','client_devices','client_sensors','custom_fields'] loop
    execute format('drop policy if exists "team insert" on public.%I', t);
    execute format('drop policy if exists "team update" on public.%I', t);
    execute format('drop policy if exists "team delete" on public.%I', t);
    if t = 'custom_fields' then execute 'create policy "team read" on public.custom_fields for select to authenticated using (true)'; end if;
    execute format('create policy "managers insert" on public.%I for insert to authenticated with check (public.can_manage(auth.uid()))', t);
    execute format('create policy "managers update" on public.%I for update to authenticated using (public.can_manage(auth.uid()))', t);
    execute format('create policy "admins delete" on public.%I for delete to authenticated using (public.has_role(auth.uid(),''admin''))', t);
  end loop;
  foreach t in array array['client_events','bypasses','shifts'] loop
    execute format('drop policy if exists "team delete" on public.%I', t);
    execute format('create policy "admins delete" on public.%I for delete to authenticated using (public.has_role(auth.uid(),''admin''))', t);
  end loop;
end $$;