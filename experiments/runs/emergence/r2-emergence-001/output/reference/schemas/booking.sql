\restrict NsMTFXcHsy5AIdzm3nkWATbUrvnjKbVeAUdZ7SndkbxHRXAiwPaBN1boygrbHN5

-- Dumped from database version 17.6 (Debian 17.6-2.pgdg12+1)
-- Dumped by pg_dump version 17.7

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: btree_gist; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA public;


--
-- Name: EXTENSION btree_gist; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION btree_gist IS 'support for indexing common datatypes in GiST';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: booking; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.booking (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ubr character varying(100) NOT NULL,
    guest_id uuid NOT NULL,
    guest_name character varying(200) NOT NULL,
    guest_email character varying(255) NOT NULL,
    guest_phone character varying(50),
    payment_intent_id character varying(100),
    customer_id character varying(100),
    currency character(3) DEFAULT 'USD'::bpchar NOT NULL,
    total_cents integer NOT NULL,
    status character varying(20) DEFAULT 'CONFIRMED'::character varying NOT NULL,
    cancellation jsonb,
    source_channel character varying(50),
    promo_code character varying(50),
    idempotency_key character varying(100),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    confirmed_at timestamp with time zone,
    cancelled_at timestamp with time zone
);


--
-- Name: booking_price; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.booking_price (
    booking_id uuid NOT NULL,
    currency character(3) DEFAULT 'USD'::bpchar NOT NULL,
    nightly_rates jsonb NOT NULL,
    subtotal_cents integer NOT NULL,
    fees jsonb NOT NULL,
    fees_total_cents integer NOT NULL,
    taxes jsonb NOT NULL,
    taxes_total_cents integer NOT NULL,
    discounts jsonb,
    discounts_total_cents integer DEFAULT 0 NOT NULL,
    total_cents integer NOT NULL
);


--
-- Name: inventory_block; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.inventory_block (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    booking_id uuid,
    property_id uuid NOT NULL,
    check_in date NOT NULL,
    check_out date NOT NULL,
    status character varying(20) DEFAULT 'CONFIRMED'::character varying NOT NULL,
    guest_first_name character varying(100),
    guest_last_name character varying(100),
    guest_email character varying(255),
    guest_phone character varying(50),
    guests integer,
    source character varying(30) NOT NULL,
    external_pms_type character varying(30),
    external_reservation_id character varying(100),
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone
);


--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_migrations (
    version character varying NOT NULL
);


--
-- Name: booking booking_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.booking
    ADD CONSTRAINT booking_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: booking booking_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.booking
    ADD CONSTRAINT booking_pkey PRIMARY KEY (id);


--
-- Name: booking_price booking_price_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.booking_price
    ADD CONSTRAINT booking_price_pkey PRIMARY KEY (booking_id);


--
-- Name: booking booking_ubr_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.booking
    ADD CONSTRAINT booking_ubr_key UNIQUE (ubr);


--
-- Name: inventory_block inventory_block_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventory_block
    ADD CONSTRAINT inventory_block_pkey PRIMARY KEY (id);


--
-- Name: inventory_block inventory_block_property_id_daterange_excl; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventory_block
    ADD CONSTRAINT inventory_block_property_id_daterange_excl EXCLUDE USING gist (property_id WITH =, daterange(check_in, check_out, '[)'::text) WITH &&) WHERE (((status)::text <> 'CANCELLED'::text));


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: idx_block_booking; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_block_booking ON public.inventory_block USING btree (booking_id) WHERE (booking_id IS NOT NULL);


--
-- Name: idx_block_external; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_block_external ON public.inventory_block USING btree (external_pms_type, external_reservation_id) WHERE (external_reservation_id IS NOT NULL);


--
-- Name: idx_block_property; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_block_property ON public.inventory_block USING btree (property_id, check_in);


--
-- Name: idx_booking_guest; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_booking_guest ON public.booking USING btree (guest_id);


--
-- Name: idx_booking_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_booking_status ON public.booking USING btree (status, created_at);


--
-- Name: booking_price booking_price_booking_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.booking_price
    ADD CONSTRAINT booking_price_booking_id_fkey FOREIGN KEY (booking_id) REFERENCES public.booking(id);


--
-- Name: inventory_block inventory_block_booking_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventory_block
    ADD CONSTRAINT inventory_block_booking_id_fkey FOREIGN KEY (booking_id) REFERENCES public.booking(id);


--
-- PostgreSQL database dump complete
--

\unrestrict NsMTFXcHsy5AIdzm3nkWATbUrvnjKbVeAUdZ7SndkbxHRXAiwPaBN1boygrbHN5


--
-- Dbmate schema migrations
--

INSERT INTO public.schema_migrations (version) VALUES
    ('20260205221043');
