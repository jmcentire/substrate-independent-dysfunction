// ============================================================
// Property Service - Type Definitions
// ============================================================

export interface PlatformDefaults {
  id: string;
  settings: Record<string, unknown>;
  created_at: Date;
  updated_at: Date;
}

export interface Brand {
  id: string;
  name: string;
  slug: string;
  settings: Record<string, unknown>;
  is_active: boolean;
  created_at: Date;
  updated_at: Date;
}

export interface Address {
  street?: string;
  city?: string;
  state?: string;
  zip?: string;
  country?: string;
}

export interface Location {
  latitude?: number;
  longitude?: number;
}

export interface Property {
  id: string;
  brand_id: string | null;
  name: string;
  slug: string;
  description: string | null;
  address: Address | null;
  location: Location | null;
  amenities: string[];
  images: string[];
  settings: Record<string, unknown>;
  status: PropertyStatus;
  is_active: boolean;
  created_at: Date;
  updated_at: Date;
}

export interface Unit {
  id: string;
  property_id: string;
  name: string;
  slug: string;
  description: string | null;
  unit_type: string | null;
  bedrooms: number;
  bathrooms: number;
  max_guests: number;
  amenities: string[];
  images: string[];
  settings: Record<string, unknown>;
  status: UnitStatus;
  is_active: boolean;
  created_at: Date;
  updated_at: Date;
}

export interface UnitWithResolvedSettings extends Unit {
  resolved_settings: Record<string, unknown>;
}

export type PropertyStatus = 'draft' | 'active' | 'inactive' | 'archived';
export type UnitStatus = 'draft' | 'active' | 'inactive' | 'archived';

// Request types
export interface CreatePropertyRequest {
  brand_id?: string;
  name: string;
  slug: string;
  description?: string;
  address?: Address;
  location?: Location;
  amenities?: string[];
  images?: string[];
  settings?: Record<string, unknown>;
  status?: PropertyStatus;
}

export interface UpdatePropertyRequest {
  brand_id?: string | null;
  name?: string;
  slug?: string;
  description?: string;
  address?: Address;
  location?: Location;
  amenities?: string[];
  images?: string[];
  settings?: Record<string, unknown>;
  status?: PropertyStatus;
  is_active?: boolean;
}

export interface CreateUnitRequest {
  name: string;
  slug: string;
  description?: string;
  unit_type?: string;
  bedrooms?: number;
  bathrooms?: number;
  max_guests?: number;
  amenities?: string[];
  images?: string[];
  settings?: Record<string, unknown>;
  status?: UnitStatus;
}

export interface UpdateUnitRequest {
  name?: string;
  slug?: string;
  description?: string;
  unit_type?: string;
  bedrooms?: number;
  bathrooms?: number;
  max_guests?: number;
  amenities?: string[];
  images?: string[];
  settings?: Record<string, unknown>;
  status?: UnitStatus;
  is_active?: boolean;
}

// API Response types
export interface ApiResponse<T> {
  success: boolean;
  data?: T;
  error?: string;
  message?: string;
}

export interface PaginatedResponse<T> {
  success: boolean;
  data: T[];
  total: number;
  page: number;
  per_page: number;
}
