import { secrets } from '@appdeploy/sdk';
import { createCipheriv, createDecipheriv, createHash, randomBytes } from 'node:crypto';
const MASTER_SECRET = 'INTEGRATION_MASTER_KEY';
export interface EncryptedSecret { algorithm: 'aes-256-gcm'; keyVersion: 1; iv: string; tag: string; ciphertext: string; }
async function masterKey() { const value = await secrets.readSecret(MASTER_SECRET); return createHash('sha256').update(value).digest(); }
export async function integrationVaultConfigured() { return (await secrets.listSecretNames()).includes(MASTER_SECRET); }
export async function encryptIntegrationSecret(value: string): Promise<EncryptedSecret> { const key = await masterKey(); const iv = randomBytes(12); const cipher = createCipheriv('aes-256-gcm', key, iv); const ciphertext = Buffer.concat([cipher.update(value, 'utf8'), cipher.final()]); return { algorithm: 'aes-256-gcm', keyVersion: 1, iv: iv.toString('base64url'), tag: cipher.getAuthTag().toString('base64url'), ciphertext: ciphertext.toString('base64url') }; }
export async function decryptIntegrationSecret(record: EncryptedSecret) { const key = await masterKey(); const decipher = createDecipheriv('aes-256-gcm', key, Buffer.from(record.iv, 'base64url')); decipher.setAuthTag(Buffer.from(record.tag, 'base64url')); return Buffer.concat([decipher.update(Buffer.from(record.ciphertext, 'base64url')), decipher.final()]).toString('utf8'); }
