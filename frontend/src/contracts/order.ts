import type { components } from '../generated/api';

export type AcceptedOrder = components['schemas']['AcceptedOrder'];
export type OrderAsset = components['schemas']['OrderAssetRead'];
export type OrderCreate = components['schemas']['OrderCreate'];
export type OrderRead = components['schemas']['OrderRead'];
export type OrderStatus = components['schemas']['OrderStatus'];
export type UploadBatchResponse = components['schemas']['UploadBatchResponse'];

export type CreateOrderPayload = Pick<
  OrderCreate,
  | 'template_id'
  | 'asset_ids'
  | 'legal_accepted'
  | 'director_mode'
  | 'global_style_text'
  | 'scene_text'
  | 'outfit_text'
  | 'scene_preset_id'
  | 'clothing_preset_id'
  | 'prompt_override'
>;

const ACTIVE_STATUSES = new Set<OrderStatus>([
  'CREATED',
  'CHECKING',
  'QUEUED',
  'GENERATING',
  'QA_PENDING',
  'REPAIRING',
]);

const DELIVERABLE_STATUSES = new Set<OrderStatus>(['READY', 'COMPLETED']);

const MANUAL_OR_FAILED_STATUSES = new Set<OrderStatus>([
  'FAILED',
  'CANCELLED',
  'UNKNOWN_EXTERNAL_STATE',
  'CONSENT_REVIEW_REQUIRED',
  'DELETED',
]);

export function isOrderInProgress(status: OrderStatus | null | undefined): boolean {
  return status ? ACTIVE_STATUSES.has(status) : false;
}

export function isOrderDeliverable(status: OrderStatus | null | undefined): boolean {
  return status ? DELIVERABLE_STATUSES.has(status) : false;
}

export function isOrderManualOrFailed(status: OrderStatus | null | undefined): boolean {
  return status ? MANUAL_OR_FAILED_STATUSES.has(status) : false;
}

export function isOrderTerminal(status: OrderStatus | null | undefined): boolean {
  return isOrderDeliverable(status) || isOrderManualOrFailed(status);
}

export function orderAssets(order: OrderRead | null | undefined): OrderAsset[] {
  return Array.isArray(order?.assets) ? order.assets : [];
}

export function previewAsset(order: OrderRead | null | undefined): OrderAsset | null {
  return orderAssets(order).find((asset) => asset.role === 'preview_watermarked') || null;
}

export function finalMasterAsset(order: OrderRead | null | undefined): OrderAsset | null {
  return orderAssets(order).find((asset) => asset.role === 'final_master') || null;
}

export function deliveryVariantAssets(order: OrderRead | null | undefined): OrderAsset[] {
  return orderAssets(order).filter((asset) => asset.role === 'delivery_variant');
}

export function displayAsset(order: OrderRead | null | undefined): OrderAsset | null {
  return (order?.can_download ? finalMasterAsset(order) : null) || previewAsset(order);
}
