import type { components } from '../generated/api';
import { httpRequest } from './http';


export type AccountExport = components['schemas']['AccountExport'];

const EXPORT_SCHEMA = 'account-export.v1';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function validateAccountExport(payload: AccountExport): AccountExport {
  if (
    payload.schema_version !== EXPORT_SCHEMA
    || !UUID.test(String(payload.export_id || ''))
    || !UUID.test(String(payload.canonical_user_id || ''))
    || Number.isNaN(Date.parse(String(payload.generated_at || '')))
  ) {
    throw new Error('The account export response is invalid.');
  }
  return payload;
}

export function accountExportFilename(payload: AccountExport): string {
  return `vowpic-account-${validateAccountExport(payload).export_id}.json`;
}

export function serializeAccountExport(payload: AccountExport): string {
  return `${JSON.stringify(validateAccountExport(payload), null, 2)}\n`;
}

export async function downloadAccountExport(): Promise<AccountExport> {
  const payload = validateAccountExport(
    await httpRequest<AccountExport>('/account/export', {
      method: 'GET',
      responseType: 'json',
    }),
  );
  if (typeof document === 'undefined' || typeof URL.createObjectURL !== 'function') {
    throw new Error('Account export download requires the Web application.');
  }
  const blob = new Blob([serializeAccountExport(payload)], {
    type: 'application/json;charset=utf-8',
  });
  const objectUrl = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement('a');
    anchor.href = objectUrl;
    anchor.download = accountExportFilename(payload);
    anchor.rel = 'noopener';
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
  return payload;
}
