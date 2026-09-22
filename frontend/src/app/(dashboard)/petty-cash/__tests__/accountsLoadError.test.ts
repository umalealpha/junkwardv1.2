import { describe, expect, test } from 'vitest'

import {
  ACCOUNTS_DENIED_MESSAGE,
  ACCOUNTS_UNAVAILABLE_MESSAGE,
  accountsLoadErrorMessage,
} from '../accountsLoadError'

// The whole point of the branch: a custodian who hits a network blip must NOT be
// told to ask Finance for a capability they already hold. Reported 2026-08-14.
describe('accountsLoadErrorMessage', () => {
  test('a 403 from the accounts list explains the missing capability', () => {
    // Arrange — the shape apiFetch throws: message from DRF `detail`, real status attached.
    const err = Object.assign(
      new Error('Financial data is restricted to finance and management staff.'),
      { status: 403 },
    )

    // Act
    const message = accountsLoadErrorMessage(err)

    // Assert
    expect(message).toBe(ACCOUNTS_DENIED_MESSAGE)
    expect(message).toContain('ask Finance')
  })

  test('a network failure tells the custodian to retry, not to ask Finance', () => {
    // Arrange — fetch rejects before any response, so there is no status.
    const err = new TypeError('Failed to fetch')

    // Act
    const message = accountsLoadErrorMessage(err)

    // Assert
    expect(message).toBe(ACCOUNTS_UNAVAILABLE_MESSAGE)
    expect(message).not.toContain('ask Finance')
  })

  test('a 500 during a deploy is transient, not a permission problem', () => {
    const err = Object.assign(new Error('HTTP 500: Internal Server Error'), { status: 500 })

    expect(accountsLoadErrorMessage(err)).toBe(ACCOUNTS_UNAVAILABLE_MESSAGE)
  })
})
