/**
 * Hand-crafted tickets used by the playground.
 *
 * Each ticket is designed to elicit a specific failure mode in the
 * agent's draft, which the human reviewer can correct and tag. The
 * mix of severities ensures both Maya and Sam (the front-line and
 * senior reviewer) have meaningful work.
 */

import type { TaskRoutingHints } from "@chap/coordinator";

export interface Ticket {
  id:            string;
  subject:       string;
  body:          string;
  customer:      string;
  expected_failure_mode: string;   // for demo narration only
  routing_hints: TaskRoutingHints;
}

export const TICKETS: Ticket[] = [
  {
    id:       "INC-48219",
    subject:  "Where is my order?",
    body:     "Hi, I placed an order four days ago (order #99481). The tracking page just says 'in transit'. Can you tell me when it'll arrive?",
    customer: "casey@example.org",
    expected_failure_mode: "bot over-apologises for what's a normal in-transit delivery",
    routing_hints: { criticality: "low", risk_tier: "standard" },
  },
  {
    id:       "INC-48220",
    subject:  "I think someone is using my account",
    body:     "I just got an email saying my password was reset. I didn't do this. There are also two orders I don't recognise in my history. Can you help? I'm worried.",
    customer: "morgan@example.org",
    expected_failure_mode: "bot may under-classify severity; this is high-priority",
    routing_hints: { criticality: "high", risk_tier: "account-security" },
  },
  {
    id:       "INC-48221",
    subject:  "Return policy question",
    body:     "Quick question — what's your return window on opened electronics? The site says 30 days for most things but I wanted to double-check before I buy a laptop.",
    customer: "priya@example.org",
    expected_failure_mode: "bot may invent specific policy details it doesn't actually have",
    routing_hints: { criticality: "low", risk_tier: "standard" },
  },
  {
    id:       "INC-48222",
    subject:  "Refund request — wrong item",
    body:     "I ordered a set of size 8 boots and received size 10. I've been waiting two weeks for the return label. This is the third time I've contacted you. I'd like a full refund AND a credit for the trouble. Order #99127.",
    customer: "alex@example.org",
    expected_failure_mode: "high-criticality refund decision; bot might commit without authority",
    routing_hints: { criticality: "high", max_cost_usd: 400, risk_tier: "refund-tier-2" },
  },
  {
    id:       "INC-48223",
    subject:  "Can you also help with my insurance claim?",
    body:     "Hi — I bought a TV from you last month and it broke. The shop says I need to file an insurance claim with my home contents policy. Can you help me with that?",
    customer: "jordan@example.org",
    expected_failure_mode: "out-of-scope; bot may try to help anyway and confuse the customer",
    routing_hints: { criticality: "low", risk_tier: "standard" },
  },
  {
    id:       "INC-48224",
    subject:  "URGENT — wrong charge on my card",
    body:     "There's a charge on my credit card for £1,840 from your store. I haven't ordered anything in months. I've already called my bank. Please confirm this isn't a real order on my account and that you'll investigate. This is urgent.",
    customer: "sam.k@example.org",
    expected_failure_mode: "critical-tier; should auto-escalate to senior on criticality alone",
    routing_hints: { criticality: "critical", risk_tier: "fraud-suspected" },
  },
];

export function getTicket(id: string): Ticket | undefined {
  return TICKETS.find((t) => t.id === id);
}
