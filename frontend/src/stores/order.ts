/**
 * Order Store - exact private-media and AcceptedOrder contract.
 */
import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import {
    type AcceptedOrder,
    type CreateOrderPayload,
    type OrderRead,
    isOrderDeliverable,
    isOrderInProgress,
    isOrderManualOrFailed,
    isOrderTerminal,
} from '../contracts/order';
import { get, post } from '../utils/api';

export type { AcceptedOrder, CreateOrderPayload, OrderRead };

export interface CreatedOrderResult {
    accepted: AcceptedOrder;
    order: OrderRead;
}

function secureIdempotencyKey(): string {
    if (globalThis.crypto?.randomUUID) {
        return `order-create-${globalThis.crypto.randomUUID()}`;
    }
    if (globalThis.crypto?.getRandomValues) {
        const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
        const suffix = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
        return `order-create-${suffix}`;
    }
    throw new Error('Secure order idempotency is unavailable');
}

export const useOrderStore = defineStore('order', () => {
    const currentOrder = ref<OrderRead | null>(null);
    const loading = ref(false);
    const pollingTimer = ref<number | null>(null);
    const pollingInFlight = ref(false);
    const pollingStartedAt = ref(0);
    let pendingCreate: { signature: string; key: string } | null = null;

    const isGenerating = computed(() => isOrderInProgress(currentOrder.value?.status));
    const isCompleted = computed(() => isOrderDeliverable(currentOrder.value?.status));
    const hasTerminalIssue = computed(() => isOrderManualOrFailed(currentOrder.value?.status));

    /**
     * Persist an exact private-asset order request and then resolve the 202 status resource.
     * The same idempotency key is retained across uncertain retries of an identical payload.
     */
    async function createOrder(payload: CreateOrderPayload): Promise<CreatedOrderResult> {
        loading.value = true;
        const signature = JSON.stringify(payload);
        if (!pendingCreate || pendingCreate.signature !== signature) {
            pendingCreate = { signature, key: secureIdempotencyKey() };
        }
        try {
            const accepted = await post<AcceptedOrder>('/orders/create', payload, {
                headers: { 'Idempotency-Key': pendingCreate.key },
            });
            const expectedStatusUrl = `/api/v1/orders/${accepted.order_id}`;
            if (accepted.status !== 'QUEUED' || accepted.status_url !== expectedStatusUrl) {
                throw new Error('Order acceptance contract is invalid');
            }
            const order = await fetchOrder(accepted.order_id);
            pendingCreate = null;
            return { accepted, order };
        } finally {
            loading.value = false;
        }
    }

    async function fetchOrder(orderId: string): Promise<OrderRead> {
        const order = await get<OrderRead>(`/orders/${orderId}`, {
            showLoading: false,
            showError: false,
        });
        currentOrder.value = order;
        return order;
    }

    async function progressOrder(orderId: string): Promise<OrderRead> {
        const order = await post<OrderRead>(
            `/orders/${orderId}/progress`,
            undefined,
            { showLoading: false, showError: false },
        );
        currentOrder.value = order;
        return order;
    }

    /**
     * Re-read a manually settled/terminal order before deciding whether work
     * remains. This lets UNKNOWN_EXTERNAL_STATE recover to READY without any
     * automatic Provider replay.
     */
    async function refreshOrder(orderId: string): Promise<OrderRead> {
        stopPolling();
        const order = await fetchOrder(orderId);
        if (isOrderInProgress(order.status)) {
            startPolling(orderId);
        }
        return order;
    }

    function nextPollingInterval(defaultIntervalMs: number): number {
        const elapsedMs = Date.now() - pollingStartedAt.value;
        if (elapsedMs < 90 * 1000) return Math.max(1500, defaultIntervalMs);
        if (elapsedMs < 8 * 60 * 1000) return 5000;
        return 10000;
    }

    function startPolling(orderId: string, intervalMs: number = 2000) {
        stopPolling();
        pollingStartedAt.value = Date.now();

        const tick = async () => {
            if (pollingInFlight.value) return;
            pollingInFlight.value = true;
            try {
                let order = currentOrder.value;
                if (!order || order.id !== orderId) {
                    order = await fetchOrder(orderId);
                }
                if (isOrderTerminal(order.status)) {
                    stopPolling();
                    return;
                }
                order = await progressOrder(orderId);
                if (isOrderTerminal(order.status)) {
                    stopPolling();
                    return;
                }
            } catch (error) {
                console.error('Polling error:', error);
            } finally {
                pollingInFlight.value = false;
            }
            pollingTimer.value = setTimeout(tick, nextPollingInterval(intervalMs)) as unknown as number;
        };

        pollingTimer.value = setTimeout(tick, Math.max(250, intervalMs)) as unknown as number;
    }

    function stopPolling() {
        if (pollingTimer.value) {
            clearTimeout(pollingTimer.value);
            pollingTimer.value = null;
        }
        pollingInFlight.value = false;
        pollingStartedAt.value = 0;
    }

    return {
        currentOrder,
        loading,
        isGenerating,
        isCompleted,
        hasTerminalIssue,
        createOrder,
        fetchOrder,
        refreshOrder,
        progressOrder,
        startPolling,
        stopPolling,
    };
});
