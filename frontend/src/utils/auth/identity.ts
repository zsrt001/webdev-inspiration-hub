/** Stable browser fingerprint used only for abuse controls, never as authentication. */

function generateUUID(): string {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
        const random = (Math.random() * 16) | 0;
        const value = char === 'x' ? random : (random & 0x3) | 0x8;
        return value.toString(16);
    });
}

export function getClientFingerprint(): string {
    let fingerprint = String(uni.getStorageSync('ai_wedding_client_fingerprint') || '').trim();
    if (!fingerprint) {
        fingerprint = generateUUID();
        uni.setStorageSync('ai_wedding_client_fingerprint', fingerprint);
    }
    return fingerprint.slice(0, 128);
}
