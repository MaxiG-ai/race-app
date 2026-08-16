/**
 * Named aliases over the generated OpenAPI types (`api.d.ts` — regenerate with
 * `npm run gen:api`, never hand-edit). Components import from here so a schema
 * change surfaces as a type error in one place rather than at every call site.
 */
import type { components } from './api'

type S = components['schemas']

export type Segment = S['Segment']
export type RaceType = S['RaceType']
export type GearKind = S['GearKind']

export type RaceListItem = S['RaceListItem']
export type RaceListResponse = S['RaceListResponse']
export type SegmentSummary = S['SegmentSummary']
export type RaceRead = S['RaceRead']
export type RaceDetail = S['RaceDetail']
export type RaceCreate = S['RaceCreate']
export type RaceUpdate = S['RaceUpdate']

export type OfficialResultRead = S['OfficialResultRead']
export type OfficialResultUpsert = S['OfficialResultUpsert']
export type RaceFuelingRead = S['RaceFuelingRead']
export type RaceFuelingUpsert = S['RaceFuelingUpsert']
export type RaceFuelingUpsertItem = S['RaceFuelingUpsertItem']
export type FuelingProduct = S['FuelingProduct']

export type RecordedActivityRead = S['RecordedActivityRead']
export type LapSplitRead = S['LapSplitRead']
export type RaceTrackResponse = S['RaceTrackResponse']
export type SegmentTrack = S['SegmentTrack']
export type TrackPoint = S['TrackPoint']

export type UploadRead = S['UploadRead']
export type UploadResult = S['UploadResult']
export type UploadTrayResponse = S['UploadTrayResponse']
export type UploadGroupProposal = S['UploadGroupProposal']
export type RaceGroupRequest = S['RaceGroupRequest']

export type GearRead = S['GearRead']
export type GearListItem = S['GearListItem']
export type GearCreate = S['GearCreate']
export type GearUpdate = S['GearUpdate']
export type UnknownDeviceRead = S['UnknownDeviceRead']
export type GearDeviceCreate = S['GearDeviceCreate']

export type StatsSummary = S['StatsSummary']
export type UserRead = S['UserRead']
