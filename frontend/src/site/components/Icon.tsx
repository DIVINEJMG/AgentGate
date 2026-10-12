import {
  BadgeCheck, BookOpenText, Boxes, BriefcaseBusiness, Check, CirclePause, Clock, Code2, Compass, Cpu, FileCheck2, FileText,
  Fingerprint, Globe, Handshake, Headset, History, KeyRound, ListOrdered, ListTodo, LockKeyhole, Megaphone, Plug, Puzzle,
  Scale, ScrollText, Shield, ShieldCheck, UserCheck, Users, type LucideIcon,
} from 'lucide-react';
import type {IconName} from '../contract/contract.generated';

const ICONS: Record<IconName, LucideIcon> = {
  workers: Users, jobs: ListTodo, governance: ShieldCheck, supervision: UserCheck, operations: BriefcaseBusiness,
  research: BookOpenText, support: Headset, sales: Handshake, marketing: Megaphone, engineering: Code2,
  identity: Fingerprint, credentials: KeyRound, pause: CirclePause, record: ScrollText, capability: Puzzle,
  policy: FileCheck2, approval: BadgeCheck, audit: History, connection: Plug, browser: Globe, document: FileText,
  shield: Shield, check: Check, clock: Clock, compass: Compass, scale: Scale, boxes: Boxes, lock: LockKeyhole,
  queue: ListOrdered, model: Cpu,
};

/** Decorative icon; the text next to it carries the meaning. */
export function Icon({name, size = 18}: {name?: IconName; size?: number}) {
  if (!name) return null;
  const Component = ICONS[name];
  return <Component size={size} strokeWidth={1.5} aria-hidden='true' focusable='false' />;
}
