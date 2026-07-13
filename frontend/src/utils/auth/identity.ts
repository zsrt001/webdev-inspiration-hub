/** Device fingerprint and JWT shape helpers. */

const CLIENT_FINGERPRINT_KEY = 'ai_wedding_client_fingerprint';

function generateRandomId(): string {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
        const random = (Math.random() * 16) | 0;
        const value = char === 'x' ? random : (random & 0x3) | 0x8;
        return value.toString(16);
    });
}

export function isJwtToken(token: string | null | undefined): boolean {
    return typeof token === 'string' && token.split('.').length === 3;
}

export function getClientFingerprint(): string {
    let fingerprint = String(uni.getStorageSync(CLIENT_FINGERPRINT_KEY) || '').trim();
    if (!fingerprint) {
        fingerprint = `device_${generateRandomId()}`;
        uni.setStorageSync(CLIENT_FINGERPRINT_KEY, fingerprint);
    }
    return fingerprint.slice(0, 128);
}
