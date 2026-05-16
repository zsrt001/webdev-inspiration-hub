/** Guest identity, client fingerprint, and JWT helpers. */

import { GUEST_ID_KEY, USER_ID_KEY } from './_keys';

function generateUUID(): string {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
        const random = (Math.random() * 16) | 0;
        const value = char === 'x' ? random : (random & 0x3) | 0x8;
        return value.toString(16);
    });
}

export function isJwtToken(token: string | null | undefined): boolean {
    return typeof token === 'string' && token.split('.').length === 3;
}

export function getGuestUserId(): string {
    const currentUserId = String(uni.getStorageSync(USER_ID_KEY) || '').trim();
    if (currentUserId.startsWith('guest_')) {
        return currentUserId;
    }

    let guestUserId = String(uni.getStorageSync(GUEST_ID_KEY) || '').trim();
    if (!guestUserId) {
        guestUserId = `guest_${generateUUID()}`;
        uni.setStorageSync(GUEST_ID_KEY, guestUserId);
    }
    return guestUserId;
}

export function getClientFingerprint(): string {
    const guestId = getGuestUserId();
    let fingerprint = String(uni.getStorageSync('ai_wedding_client_fingerprint') || '').trim();
    if (!fingerprint) {
        fingerprint = `${guestId}:${Date.now()}:${Math.random().toString(16).slice(2)}`;
        uni.setStorageSync('ai_wedding_client_fingerprint', fingerprint);
    }
    return fingerprint.slice(0, 128);
}
