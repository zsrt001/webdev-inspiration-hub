import { post } from './api';

type AnalyticsMeta = Record<string, unknown>;

interface TrackEventInput {
    eventType: string;
    sourcePage: string;
    templateId?: string | null;
    meta?: AnalyticsMeta | null;
}

export async function trackEvent(input: TrackEventInput): Promise<void> {
    const eventType = String(input.eventType || '').trim();
    const sourcePage = String(input.sourcePage || '').trim();
    if (!eventType || !sourcePage) return;

    try {
        await post('/analytics/click', {
            event_type: eventType,
            source_page: sourcePage,
            template_id: input.templateId || null,
            meta: input.meta || null,
        }, { showLoading: false, showError: false } as any);
    } catch {
        // Analytics must never block creation, preview, download, or payment flows.
    }
}

export default trackEvent;
