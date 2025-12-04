'use strict';

const express = require('express');
const router = express.Router();
const axios = require('axios');
const libsLoader = require('../../lib/libraries-loader');

const MODAL_VALIDATION_URL = process.env.MODAL_VALIDATION_URL || 'https://drlalithapranathi--elm-validator-fastapi-app.modal.run/validate';

/**
 * GET CDR endpoint for OpenEMR
 * Validates ELM with Modal and returns it if valid
 */

router.post('/:libraryId', getCDR);
router.post('/:libraryId/version/:version', getCDR);

async function getCDR(req, res) {
  const libraryId = req.params.libraryId;
  const version = req.params.version;

  console.log(`[Get CDR] Request for ${libraryId} ${version || '(latest)'}`);

  // Step 1: Get ELM from memory (already loaded)
  let lib;
  if (version) {
    lib = libsLoader.get().resolve(libraryId, version);
  } else {
    lib = libsLoader.get().resolveLatest(libraryId);
  }

  if (!lib) {
    console.error(`[Get CDR] Library not found: ${libraryId}`);
    res.status(404).json({
      success: false,
      error: `Library not found: ${libraryId} ${version || '(latest)'}`
    });
    return;
  }

  const elmJson = lib.source;
  const libraryName = elmJson.library.identifier.id;

  console.log(`[Get CDR] Found library: ${libraryName}`);

  // Step 2: Validate with Modal
  if (!MODAL_VALIDATION_URL) {
    console.warn('[Get CDR] MODAL_VALIDATION_URL not set, skipping validation');
    // Return ELM without validation
    res.json({
      success: true,
      validated: false,
      elm_json: elmJson,
      library_id: libraryId,
      library_version: elmJson.library.identifier.version,
      library_name: libraryName,
      warning: 'Validation skipped (Modal URL not configured)'
    });
    return;
  }

  try {
    console.log(`[Get CDR] Validating with Modal: ${MODAL_VALIDATION_URL}`);

    const validationResponse = await axios.post(MODAL_VALIDATION_URL, {
      elm_json: elmJson,
      library_name: libraryName
    }, {
      timeout: 60000,
      headers: { 'Content-Type': 'application/json' }
    });

    const validationResult = validationResponse.data;

    console.log(`[Get CDR] Validation result:`, validationResult);

    // Step 3: Return result
    if (validationResult.valid) {
      console.log(`[Get CDR] ✓ Validation passed for ${libraryName}`);

      res.json({
        success: true,
        validated: true,
        valid: true,
        elm_json: elmJson,
        library_id: libraryId,
        library_version: elmJson.library.identifier.version,
        library_name: libraryName,
        validation_result: validationResult
      });
    } else {
      console.error(`[Get CDR] ✗ Validation failed for ${libraryName}`);

      res.status(400).json({
        success: false,
        validated: true,
        valid: false,
        errors: validationResult.errors,
        warnings: validationResult.warnings,
        library_id: libraryId,
        library_name: libraryName
      });
    }

  } catch (error) {
    console.error(`[Get CDR] Error validating ${libraryName}:`, error.message);

    // If Modal is down, return ELM without validation
    if (error.code === 'ECONNREFUSED' || error.code === 'ETIMEDOUT') {
      console.warn('[Get CDR] Modal unavailable, returning ELM without validation');

      res.json({
        success: true,
        validated: false,
        elm_json: elmJson,
        library_id: libraryId,
        library_version: elmJson.library.identifier.version,
        library_name: libraryName,
        warning: 'Validation skipped (Modal API unavailable)'
      });
    } else {
      res.status(500).json({
        success: false,
        error: error.message
      });
    }
  }
}

module.exports = router;
