import { useState } from 'react'
import { zodResolver } from '@hookform/resolvers/zod'
import { useForm } from 'react-hook-form'
import { useLocation, useNavigate } from 'react-router-dom'
import { z } from 'zod'
import { ApiError } from '@/api/client'
import { login, useInvalidateCurrentUser } from '@/api/auth'

const loginSchema = z.object({
  email: z.string().min(1, 'Enter your email.').email('Enter a valid email address.'),
  password: z.string().min(1, 'Enter your password.'),
})

type LoginValues = z.infer<typeof loginSchema>

/** Login is the only unauthenticated route (§5 IA); everywhere else redirects
 * here on 401. Plain, functional, single centered card — no marketing copy. */
export default function Login() {
  const navigate = useNavigate()
  const location = useLocation()
  const invalidateCurrentUser = useInvalidateCurrentUser()
  const [formError, setFormError] = useState<string | null>(null)

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({ resolver: zodResolver(loginSchema) })

  const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname ?? '/'

  const onSubmit = async (values: LoginValues) => {
    setFormError(null)
    try {
      await login(values)
      await invalidateCurrentUser()
      navigate(from, { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setFormError('Too many attempts. Wait a few minutes and try again.')
      } else if (err instanceof ApiError && err.status === 401) {
        setFormError('Incorrect email or password.')
      } else {
        setFormError('Something went wrong. Please try again.')
      }
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg px-4">
      <div className="w-full max-w-sm rounded-lg border border-hairline p-8">
        <h1 className="mb-6 text-lg font-semibold text-ink">Racing History</h1>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
          <div>
            <label htmlFor="email" className="mb-1 block text-sm text-ink/70">
              Email
            </label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              className="w-full rounded border border-hairline bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-ink/40"
              {...register('email')}
            />
            {errors.email && <p className="mt-1 text-xs text-run">{errors.email.message}</p>}
          </div>

          <div>
            <label htmlFor="password" className="mb-1 block text-sm text-ink/70">
              Password
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              className="w-full rounded border border-hairline bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-ink/40"
              {...register('password')}
            />
            {errors.password && <p className="mt-1 text-xs text-run">{errors.password.message}</p>}
          </div>

          {formError && <p className="text-sm text-run">{formError}</p>}

          <button
            type="submit"
            disabled={isSubmitting}
            className="w-full rounded bg-ink px-3 py-2 text-sm font-medium text-bg transition-opacity disabled:opacity-50"
          >
            {isSubmitting ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}
