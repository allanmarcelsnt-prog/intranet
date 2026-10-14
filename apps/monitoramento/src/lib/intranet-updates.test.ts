import { describe, expect, it } from 'vitest';
import { adjacentReportWeek, buildClientBlock, handoverRecords, isDisarmed, shiftContainsEvent, toISODate, weekDays, weekdayLabel, type EventRow } from './safenet';

const event = (p: Partial<EventRow>): EventRow => ({
  id: '1', client_id: 'c', kind: 'arm', event_date: '2026-10-06', event_time: '18:00:00',
  user_name: 'Fulano', device_name: 'Controle', by_operator: false, sensor_id: null,
  sensor_ids: [], sensor_labels: null, end_time: null, custom: {}, description: null,
  status: null, operator_id: null, archived: false, created_at: '2026-10-06T18:00:00Z', ...p,
});
const oldDisarm = event({ id: 'd', kind: 'disarm', event_date: '2026-10-05', event_time: '06:49:00' });
const oldArm = event({ id: 'a', event_date: '2026-10-05' });
const shift = { shiftType: 'Diurno', operatorName: 'Lucas', nextShiftType: 'Noturno', nextOperatorName: 'Daniel' };

describe('estado registrado', () => {
  it('desarme mais recente indica desarmado, mesmo com horário menor no dia seguinte', () => {
    expect(isDisarmed([oldArm, event({ kind: 'disarm', event_time: '06:49:00' })])).toBe(true);
  });
  it('arme posterior remove indicação', () => {
    expect(isDisarmed([oldDisarm, oldArm])).toBe(false);
  });
  it('sem registros não presume desarmado', () => expect(isDisarmed([])).toBe(false));
});

describe('contexto de passagem', () => {
  it('sem arme/desarme usa ambos os anteriores com data, responsável e dispositivo', () => {
    const msg = buildClientBlock({ clientName: 'X', date: '2026-10-06', events: [], previousEvents: [oldDisarm, oldArm], newBypasses: [], sensorLabel: () => '' }, shift);
    expect(msg).toContain('Desarme: 05/10/2026 06:49 por Fulano — dispositivo: Controle (registro anterior)');
    expect(msg).toContain('Arme: 05/10/2026 18:00 por Fulano — dispositivo: Controle (registro anterior)');
  });
  it('apenas arme complementa com último desarme', () => {
    const arm = event({});
    expect(handoverRecords([arm], [oldDisarm, oldArm])).toMatchObject({ arm, disarm: oldDisarm, previousDisarm: true, previousArm: false });
  });
  it('apenas desarme não recupera último arme', () => {
    expect(handoverRecords([event({ kind: 'disarm' })], [oldArm]).arm).toBeUndefined();
  });
  it('ambos no plantão prevalecem sobre histórico', () => {
    const disarm = event({ kind: 'disarm', event_time: '06:49:00' });
    const arm = event({});
    expect(handoverRecords([disarm, arm], [oldDisarm, oldArm])).toMatchObject({ disarm, arm, previousDisarm: false, previousArm: false });
  });
  it('não inventa históricos inexistentes', () => expect(handoverRecords([])).toMatchObject({ arm: undefined, disarm: undefined }));
  it('registro anterior no mesmo dia não é do plantão atual', () => {
    const start = new Date(2026, 9, 6, 7);
    expect(shiftContainsEvent(event({ event_time: '06:49:00' }), start.toISOString(), new Date(2026, 9, 6, 19))).toBe(false);
    expect(shiftContainsEvent(event({ event_time: '08:00:00' }), start.toISOString(), new Date(2026, 9, 6, 19))).toBe(true);
  });
});

describe('semana cortada no mês', () => {
  it('encerra em 31/10/2026 e reinicia em 01/11/2026, sem pular dias', () => {
    const ref = new Date(2026, 9, 30);
    expect(weekDays(ref)).toEqual(['2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30', '2026-10-31']);
    const next = adjacentReportWeek(ref, 1);
    expect(toISODate(next)).toBe('2026-11-01');
    expect(weekDays(next)).toEqual(['2026-11-01']);
    expect(weekdayLabel('2026-11-01')).toBe('Dom');
    expect(toISODate(adjacentReportWeek(next, 1))).toBe('2026-11-02');
    expect(toISODate(adjacentReportWeek(next, -1))).toBe('2026-10-31');
  });
  it('divide a semana de setembro/outubro e mantém o dia da semana correto', () => {
    expect(weekDays(new Date(2026, 8, 30))).toEqual(['2026-09-28', '2026-09-29', '2026-09-30']);
    expect(weekDays(new Date(2026, 9, 1))).toEqual(['2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04']);
    expect(weekdayLabel('2026-10-01')).toBe('Qui');
  });
  it('considera fevereiro bissexto e a virada do ano', () => {
    expect(weekDays(new Date(2028, 1, 29)).at(-1)).toBe('2028-02-29');
    expect(toISODate(adjacentReportWeek(new Date(2026, 11, 31), 1))).toBe('2027-01-01');
  });
});
