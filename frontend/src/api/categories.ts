import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './client'
import type { RaceCategoryRead } from '@/types'

export const categoryKeys = {
  all: ['categories'] as const,
}

/**
 * The distance-category table (§9). Effectively static — it changes only when a
 * migration corrects a boundary — so it is cached for the session rather than refetched
 * alongside every race.
 */
export function useCategories() {
  return useQuery({
    queryKey: categoryKeys.all,
    queryFn: () => apiFetch<RaceCategoryRead[]>('/categories'),
    staleTime: Infinity,
  })
}
