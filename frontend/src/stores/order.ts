/**
 * Order Store - Pinia
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { get, post } from '../utils/api';

export type OrderStatus =
    | 'CREATED'
    | 'CHECKING'
    | 'GENERATING'
    | 'COMPLETED';

export interface Order {
    id: string;
    user_id: string;
    status: OrderStatus;
    template_id: string;
    generation_params?: Record<string, any> | null;
    source_image_urls: { images: string[] } | null;
    preview_image_urls: Record<string, string> | null;
    final_image_urls: Record<string, string> | null;
    can_download?: boolean;
    access_tier?: string | null;
    download_locked?: boolean;
    price_cents: number;
    error_message: string | null;
    director_mode?: boolean | null;
    remote_join?: boolean | null;
    subject_count?: number | null;
    couple_flow?: string | null;
    effective_scene_source?: string | null;
    effective_outfit_source?: string | null;
    effective_scene_preset_id?: string | null;
    effective_outfit_preset_id?: string | null;
    effective_scene_preset_title?: string | null;
    effective_outfit_preset_title?: string | null;
    effective_scene_ip_weight?: number | null;
    effective_outfit_ip_weight?: number | null;
    ignored_inputs?: string[] | null;
    director_summary?: Record<string, any> | null;
    director_decision_hints?: string[] | null;
    couple_guardrails?: Record<string, any> | null;
    qa_last_reasons?: string[] | null;
    qa_attempt_count?: number | null;
    failure_code?: string | null;
    failure_provider?: string | null;
    created_at: string;
    updated_at: string;
}

interface CreateOrderRequest {
    template_id: string;
    user_images: string[];
    legal_accepted?: boolean;
    director_mode?: boolean;
    remote_join?: boolean;
    global_style_text?: string;
    scene_text?: string;
    outfit_text?: string;
    scene_preset_id?: string;
    clothing_preset_id?: string;
    prompt_override?: string;
    scene_image_url?: string;
    clothing_image_url?: string;
    pose_image_url?: string;
    depth_image_url?: string;
    normal_image_url?: string;
    scene_ip_weight?: number;
    clothing_ip_weight?: number;
    face_ip_weight?: number;
    pose_cn_weight?: number;
    depth_cn_weight?: number;
    normal_cn_weight?: number;
    pose_cn_start?: number;
    pose_cn_end?: number;
    depth_cn_start?: number;
    depth_cn_end?: number;
    normal_cn_start?: number;
    normal_cn_end?: number;
}

export const useOrderStore = defineStore('order', () => {
    const currentOrder = ref<Order | null>(null);
    const loading = ref(false);
    const pollingTimer = ref<number | null>(null);
    const pollingInFlight = ref(false);

    const isGenerating = computed(() => {
        const status = currentOrder.value?.status;
        if (currentOrder.value?.error_message) return false;
        return status === 'CHECKING' || status === 'GENERATING';
    });

    const isCompleted = computed(() => {
        return currentOrder.value?.status === 'COMPLETED';
    });

    /**
     * Create a new order
     */
    async function createOrder(
        templateId: string,
        userImages: string[],
        options: Partial<CreateOrderRequest> = {}
    ): Promise<Order> {
        loading.value = true;
        try {
            const order = await post<Order>('/orders/create', {
                template_id: templateId,
                user_images: userImages,
                ...options,
            });
            currentOrder.value = order;
            return order;
        } finally {
            loading.value = false;
        }
    }

    /**
     * Fetch order by ID
     */
    async function fetchOrder(orderId: string): Promise<Order> {
        const order = await get<Order>(`/orders/${orderId}`, { showLoading: false, showError: false });
        currentOrder.value = order;
        return order;
    }

    /**
     * Start polling order status
     */
    function startPolling(orderId: string, intervalMs: number = 2000) {
        stopPolling();

        pollingTimer.value = setInterval(async () => {
            if (pollingInFlight.value) return;
            pollingInFlight.value = true;
            try {
                await fetchOrder(orderId);

                const hasTerminalError = !!currentOrder.value?.error_message;
                if (currentOrder.value?.status === 'COMPLETED' || hasTerminalError) {
                    stopPolling();
                }
            } catch (error) {
                console.error('Polling error:', error);
            } finally {
                pollingInFlight.value = false;
            }
        }, intervalMs) as unknown as number;
    }

    /**
     * Stop polling
     */
    function stopPolling() {
        if (pollingTimer.value) {
            clearInterval(pollingTimer.value);
            pollingTimer.value = null;
        }
        pollingInFlight.value = false;
    }

    return {
        currentOrder,
        loading,
        isGenerating,
        isCompleted,
        createOrder,
        fetchOrder,
        startPolling,
        stopPolling,
    };
});
