# Billing — Product Requirements Document

## 1. Overview
Billing lets customers pay for plans and add-ons. This document covers the Q4 release.

## 2. Goals
- Reduce failed payments by 20%

## 3. Features and requirements

### 3.1 Invoices
- REQ-1: The system shall generate an invoice PDF for every successful payment.
- REQ-2: Users can download past invoices from the billing page.
- REQ-3: Invoices must show tax, discounts and the billing address.

### 3.2 Usage limits
- The system shall warn the account owner when usage reaches 80% of the plan limit.
- Users must be able to see current usage limits on the dashboard.

### 3.3 Payment methods
| ID | Requirement | Priority |
|----|-------------|----------|
| FR-7 | Support card payments through the payment provider webhook | Must have |
| FR-8 | Allow users to update their saved card | Should have |
| FR-9 | Support invoice-based payment for enterprise accounts | Could have |

## 4. Out of scope
- Crypto payments must not be supported in this release.

## 5. Open questions
- Should refunds be self-serve?
